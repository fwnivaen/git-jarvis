"""Executor ownership boundaries. Core imports remain standard-library only."""
import json
import os
from pathlib import Path
import queue
import secrets
import signal
import subprocess
import threading
import time

from execution_control import ControlError
from helper_protocol import frame, parse, MAX_FRAME_BYTES
import usage_policy

ROOT = Path(__file__).resolve().parent
POLL_S = .05
TERMINATE_S = 2
KILL_S = 2
MAX_HELPER_EVENTS = 256
MAX_RUN_SECONDS = 120
MAX_CHILD_CALLS = 8
MAX_QUESTION_CHARS = 2000
PROTOCOL_KEY_BYTES = 32
READ_TOOLS = frozenset({'notes_search','note_read','preferences_get'})


class LangGraphAdapter:
    """Existing graph remains its own planner; never nest it under another boss."""
    def __init__(self, service):
        self.service=service

    def capabilities(self):
        return {'start':True,'status':True,'events':True,'cancel':True,'resume':True,'pause':False,'watch':False}

    def start(self, **kwargs):
        result=self.service.start(**kwargs);self.bound=result['id'];return result
    def status(self, run_id=None):
        run_id=run_id or self.bound
        result=self.service.detail(run_id)
        result['settled']=self.service.settled(run_id)
        return result
    def cancel(self, run_id=None):return self.service.cancel(run_id or self.bound)
    def resume(self, run_id):return self.service.start(resume=run_id)
    def events(self, run_id, after=0):
        run=self.status(run_id)
        return {'run':run,'events':[e for e in run['events'] if e['id']>after],
                'reset':bool(run['events'] and after<run['events'][0]['id']-1)}


class DeepAgentsAdapter:
    """One SDK process per run; no CodexBridge-as-BaseChatModel conversion.

    Live inference is blocked by the included-usage policy, even if a key exists.
    Fixtures use a scripted model, never CodexBridge as a raw model. Token goes only to
    the helper, never the model. Whole process group termination is confirmed.
    """
    def __init__(self, python, gateway, store, run_id, token, validator):
        self.python,self.gateway,self.store=Path(python),gateway,store
        self.run_id,self.token,self.validator=run_id,token,validator
        self.process=None
        self.thread=None
        self.cancelled=threading.Event()
        self.settled=threading.Event()
        self.records=[]
        self.result=None
        self.error=None
        self.state='queued'
        self.lock=threading.RLock()
        self.in_sequence=self.out_sequence=0
        self.key=secrets.token_urlsafe(PROTOCOL_KEY_BYTES)

    def capabilities(self):
        return {'start':True,'status':True,'events':True,'cancel':True,'resume':False,'pause':False,'watch':False}

    def start(self, question, *, model=None, fixture=False, scenario='research', duration_s=MAX_RUN_SECONDS):
        if self.process is not None or not self.python.is_file():
            raise ControlError('Helper unavailable or already started.')
        run=self.store.get(self.run_id)
        if run['engine'] != 'deepagents' or run['status']!='running' or not 0<duration_s<=MAX_RUN_SECONDS:
            raise ControlError('Run owner or duration mismatch.')
        if not isinstance(question,str) or not 0<len(question)<=MAX_QUESTION_CHARS:
            raise ControlError('Supply a bounded research question.')
        actor=self.store.authenticate(self.token,self.run_id)
        if fixture is not True:
            raise ControlError(usage_policy.ERROR_MESSAGES['subscription_required'] +
                ' Live Deep Agents has no verified included-usage model adapter and remains disabled.')
        self.child_token=self.store.issue(self.run_id,parent_token=self.token,
            permissions=READ_TOOLS & set(actor['permissions']),max_calls=min(MAX_CHILD_CALLS,actor['max_calls']))
        env={k:os.environ[k] for k in ('PATH','TMPDIR','SYSTEMROOT') if k in os.environ}
        env.update(JARVIS_HELPER_TOKEN=self.key, LANGSMITH_TRACING='false', LANGCHAIN_TRACING_V2='false',
                   PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1')
        self.deadline=time.monotonic()+min(duration_s,max(0,run['expires_at']-time.time()))
        self.process=subprocess.Popen([str(self.python),'-I',str(ROOT/'optional/deepagents/helper.py')],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,
            env=env,cwd=ROOT/'optional/deepagents',start_new_session=True)
        self.state='running'
        self._send('start',dict(question=question,fixture=fixture,scenario=scenario,model=model,
                               duration_s=min(duration_s,max(0,run['expires_at']-time.time()))))
        self.thread=threading.Thread(target=self._run,daemon=True,name='jarvis-sdk-owner')
        self.thread.start()
        def supervise():
            # The RPC callback may outlive its deadline. Kill the helper
            # independently, but keep ownership until that callback settles.
            while not self.settled.wait(POLL_S):
                if self.cancelled.is_set() or time.monotonic()>=self.deadline:
                    try:self._terminate()
                    except Exception:pass  # Owner retains cleanup-pending state.
                    break
        threading.Thread(target=supervise,daemon=True,name='jarvis-sdk-expiry').start()
        return self.status()

    def _send(self,kind,data):
        self.out_sequence+=1
        self.process.stdin.write(frame(self.key,self.out_sequence,kind,data))
        self.process.stdin.flush()

    def _terminate(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                try:os.killpg(self.process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:self.process.wait(timeout=TERMINATE_S)
                except subprocess.TimeoutExpired:
                    try:os.killpg(self.process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    self.process.wait(timeout=KILL_S)

    def _run(self):
        messages=queue.Queue(maxsize=MAX_HELPER_EVENTS)
        def read():
            try:
                while True:
                    line=self.process.stdout.readline(MAX_FRAME_BYTES+1)
                    if not line:break
                    messages.put(line,timeout=POLL_S)
            except Exception:
                pass
            finally:
                try:messages.put(None,timeout=POLL_S)
                except queue.Full:pass
        reader=threading.Thread(target=read,daemon=True);reader.start()
        try:
            while True:
                if self.cancelled.is_set():raise ControlError('cancelled')
                if time.monotonic()>=self.deadline:raise ControlError('deadline')
                try:line=messages.get(timeout=POLL_S)
                except queue.Empty:continue
                if line is None:raise ControlError('helper_disconnected')
                self.in_sequence+=1
                message=parse(self.key,self.in_sequence,line)
                kind,data=message['kind'],message['data']
                if kind=='tool':
                    if data['name'] not in READ_TOOLS:raise ControlError('undeclared_tool')
                    # Operation identity and authority are controller-owned.
                    if type(data.get('worker')) is not bool:raise ControlError('worker_identity_missing')
                    actor_token=self.child_token if data['worker'] else self.token
                    result=self.gateway.invoke(actor_token,self.run_id,data['name'],data['arguments'],self.deadline,self.cancelled)
                    self._send('tool_result',{'result':result})
                elif kind=='result':
                    self.store.authenticate(self.token,self.run_id)
                    self.validator(data)
                    self.result=data
                    self.state='complete'
                    break
                elif kind=='error':raise ControlError('sdk_failure')
                elif kind in {'started','usage','step'}:
                    if len(self.records)>=MAX_HELPER_EVENTS:raise ControlError('event_budget')
                    # Keep only minimized runtime metadata, never tool arguments.
                    self.records.append({'id':len(self.records)+1,'kind':kind,'data':data if kind=='usage' else {}})
                else:raise ControlError('protocol_failure')
        except Exception:
            self.state='cancelled' if self.cancelled.is_set() else 'failed'
            self.error='cancelled' if self.cancelled.is_set() else 'sdk_or_gateway_failure'
            self.result=None
        finally:
            try:self._terminate()
            except Exception:
                self.state='interrupted';self.error='cleanup_pending';self.result=None
            for stream in (self.process.stdin,self.process.stdout):stream.close()
            if self.process.poll() is not None:
                self.settled.set()

    def status(self):
        usage=[row['data']['total_tokens'] for row in self.records if row['kind']=='usage']
        return {'id':self.run_id,'engine':'deepagents','status':self.state,'result':self.result,'error':self.error,
                'total_tokens':sum(usage) if usage else None,'usage_complete':bool(usage) and self.state=='complete',
                'settled':self.settled.is_set() or self.process is None,'capabilities':self.capabilities()}

    def events(self,after=0):
        return [r for r in self.records if r['id']>after]

    def cancel(self):
        self.cancelled.set()
        if self.store.get(self.run_id)['status']=='running':self.store.transition(self.run_id,'cancelling')
        return self.status()

    def resume(self,*_args):
        raise ControlError('Safe SDK resume is unsupported; start a fresh bounded run.')
