"""Canonical task views and side-effect-free previews of the existing Work engine.

This is an adapter, not another scheduler or task store. HTTP handlers remain
responsible for local-origin checks and strict request-field validation.
"""
import copy

from ultra_workflows import workflow_template
from workflow_runtime import (MAX_QUESTION_CHARS, MAX_WORKERS, MAX_TOOL_CALLS,
    MAX_RESTARTS, MAX_RUNS, READ_TOOLS, WORKER_COUNTS, WORKFLOW_LABELS, WorkflowError)

SCHEMA_VERSION = 1
TASK_FIELDS = ('id', 'workflow', 'question', 'status', 'phase', 'cleanup', 'error',
    'created_at', 'elapsed_s', 'remaining_s', 'agents', 'tasks', 'tool_calls',
    'total_tokens', 'usage_complete', 'model_usage', 'sequence', 'events',
    'restarts', 'can_retry', 'result')
EXECUTION_LIMITS = (
    'Analysis uses the local notes and registered read-only tools; it cannot browse the web, '
        'run code, edit files, send messages, or export documents.',
    'This preview does not read source contents or verify model login, installed runtime, '
        'storage capacity, or source availability.',
    'Starting work is a separate request and rechecks settings, admission capacity, and tool availability.',
    'Cancellation fences results promptly; capacity stays occupied until underlying callbacks settle.',
    'Recovery uses the original time and attempt budgets and rechecks saved source revisions.',
    'General action approvals and automatic undo are not implemented by this engine.',
)


class TaskAPI:
    def __init__(self, service):
        self.service = service

    def _task(self, run, owned=None):
        result = {key: copy.deepcopy(run[key]) for key in TASK_FIELDS if key in run}
        owned = not self.service.settled(run['id']) if owned is None else owned
        result['engine'] = 'LangGraph'
        result['settled'] = not owned
        result['capabilities'] = {
            'cancel': owned and run['status'] == 'running',
            'resume': bool(run.get('can_retry')),
            'pause': False, 'watch': False, 'approve': False, 'undo': False,
        }
        return result

    def catalog(self):
        """Delegate storage/recovery to WorkflowService; never admit or run work."""
        source = self.service.catalog()
        return {
            'schema_version': SCHEMA_VERSION,
            'engine': source['engine'],
            'enabled': source['enabled'], 'available': source['available'],
            'notice': source.get('notice'), 'capacity_notice': source.get('capacity_notice'),
            'active_ids': list(source.get('active_ids', [])),
            'limits': copy.deepcopy(source['limits']),
            'workflows': copy.deepcopy(source['workflows']),
            'capabilities': {
                'start': source['enabled'] and source['available'],
                'cancel': True, 'resume': True, 'plan': True,
                'pause': False, 'watch': False, 'approve': False, 'undo': False,
            },
            'tasks': [self._task(run, run['id'] in source.get('active_ids', [])) for run in source['runs']],
        }

    def detail(self, task_id):
        return self._task(self.service.detail(task_id))

    def plan(self, question, workflow='research'):
        """Inspect recipe/settings metadata only; no initialization or execution."""
        if (not isinstance(question, str) or not question.strip()
                or len(question) > MAX_QUESTION_CHARS
                or not isinstance(workflow, str) or workflow not in WORKFLOW_LABELS):
            raise WorkflowError('Supply a short question and a known workflow.')
        settings = self.service.settings()
        recipe = workflow_template(workflow=workflow)
        worker_count = min(WORKER_COUNTS[workflow], settings.max_workers, MAX_WORKERS)
        # available() returns declarations. It does not call any registered tool.
        permitted = sorted(READ_TOOLS & {item['name'] for item in self.service.tools.available()})
        adaptive = settings.adaptive_routing_enabled
        return {
            'schema_version': SCHEMA_VERSION, 'dry_run': True,
            'admitted': False, 'engine': 'LangGraph',
            'question': self.service.redactor.text(question.strip(), MAX_QUESTION_CHARS),
            'workflow': recipe['id'], 'label': recipe['label'],
            'roles': recipe['roles'][:worker_count], 'criteria': recipe['criteria'],
            'steps': [
                {'phase': 'retrieve', 'description': 'Search the local notes for the question.',
                    'tool': 'notes_search'},
                {'phase': 'assign', 'description': 'Assign the selected recipe roles.',
                    'workers': worker_count},
                {'phase': 'workers', 'description': 'Analyze source evidence with the permitted read-only tools.'},
                {'phase': 'review', 'description': 'Validate the answer, source evidence, and arithmetic.',
                    'model_review_may_be_skipped': adaptive and workflow == 'general'},
            ],
            'permitted_tools': permitted,
            'execution_enabled': settings.enabled and settings.workflows_enabled and settings.tools_enabled,
            'prerequisites': {
                'notes_search_registered': 'notes_search' in permitted,
                'runtime_checked': False, 'model_login_checked': False,
                'source_contents_checked': False, 'storage_capacity_checked': False,
            },
            'limits': {
                'concurrent_tasks': self.service.max_concurrent,
                'workers': worker_count, 'shared_model_calls': MAX_WORKERS,
                'shared_tool_calls': MAX_WORKERS, 'tool_calls_per_task': MAX_TOOL_CALLS,
                'request_timeout_s': settings.request_timeout_s,
                'worker_timeout_s': settings.worker_timeout_s,
                'worker_retries': settings.worker_retries, 'max_restarts': MAX_RESTARTS,
                'max_saved_runs': MAX_RUNS,
                'adaptive_model_budget_enforced': adaptive,
                'model_calls_per_task': settings.model_call_limit if adaptive else None,
                'observed_tokens_per_task': settings.observed_token_limit if adaptive else None,
            },
            'side_effects': {
                'preview': {'model_calls': False, 'tool_calls': False, 'task_admission': False,
                    'database_creation': False, 'note_writes': False},
                'execution': {'model_calls': True, 'local_note_reads': True,
                    'private_checkpoints': True, 'private_tool_journal': True,
                    'stores_question_and_results': True, 'note_writes': False,
                    'external_actions': False},
            },
            'checkpoint_storage': {
                'location': 'Private local Work catalog, graph checkpoints, and per-task tool journals.',
                'directory_mode': '0700', 'database_mode': '0600',
                'created_by_preview': False, 'contains_source_passages': True,
            },
            'execution_limits': list(EXECUTION_LIMITS),
            'capabilities': {'plan': True, 'execute_preview': False,
                'approve': False, 'undo': False, 'external_actions': False},
        }
