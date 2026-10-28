"""Bounded metadata projection of existing activity; no new log or scheduler.

Callbacks must supply catalogs/events already scoped to the local user. Raw
questions, answers, note contents, arguments, paths and process data are never
copied into this projection. Missing sources remain explicitly unavailable.
"""
import hashlib
import json
import math
import re
import time

SCHEMA_VERSION = 1
DEFAULT_ITEMS = 50
MAX_ITEMS = 100
MAX_WORKFLOWS = 50
MAX_APPROVALS = 64
MAX_EVENTS_PER_RUN = 64
MAX_TOOL_EVENTS = 256
MAX_CLOCK_SKEW_S = 60
MAX_EVENT_SEQUENCE = 1_000_000_000
MAX_COUNT = 1_000_000_000_000
IDENTIFIER = re.compile(r'[a-f0-9]{24}\Z')
WORKFLOWS = {'general': 'Focused answer', 'research': 'Evidence research',
    'document': 'Document analysis', 'code': 'Code review'}
STATUSES = frozenset({'queued', 'running', 'waiting', 'cancelling', 'complete',
    'failed', 'cancelled', 'interrupted'})
PHASES = frozenset({'retrieve', 'assign', 'workers', 'review', 'complete'})
WORKFLOW_EVENTS = {
    'started': 'Work started', 'resumed': 'Work resumed', 'node_complete': 'Work step completed',
    'agent_started': 'Analysis started', 'agent_complete': 'Analysis completed',
    'agent_failed': 'Analysis stopped', 'tool_started': 'Read tool started',
    'tool_complete': 'Read tool completed', 'tool_failed': 'Read tool stopped',
    'usage': 'Usage updated', 'complete': 'Work completed', 'failed': 'Work failed',
    'cancel_requested': 'Stop requested', 'cancelled': 'Work cancelled', 'interrupted': 'Work interrupted',
}
APPROVAL_EVENTS = {
    'pending': 'Note edit awaiting review', 'applying': 'Approved note edit is being saved',
    'rejected': 'Note edit rejected', 'applied': 'Approved note edit saved',
    'expired': 'Note edit approval expired', 'stale': 'Note edit refused because its source changed',
    'uncertain': 'Note edit outcome needs inspection', 'undoing': 'Note edit undo is being saved',
    'undone': 'Note edit undone', 'undo_stale': 'Undo refused because the note changed',
    'undo_uncertain': 'Undo outcome needs inspection',
}
APPROVAL_STATUSES = frozenset(APPROVAL_EVENTS)
PREFERENCE_EVENTS = {
    'proposed': 'Preference change proposed', 'approved': 'Preference change approved',
    'pending': 'Preference change awaiting review', 'applying': 'Approved preference change is being saved',
    'rejected': 'Preference change rejected', 'applied': 'Approved preference change saved',
    'expired': 'Preference approval expired', 'stale': 'Preference change refused because memory changed',
    'uncertain': 'Preference change outcome needs inspection', 'undoing': 'Preference undo is being saved',
    'undone': 'Preference change undone', 'undo_stale': 'Preference undo refused because memory changed',
    'undo_uncertain': 'Preference undo outcome needs inspection',
}
APPROVAL_EVENTS.update({'preference_' + kind: label for kind, label in PREFERENCE_EVENTS.items()})
TOOL_EVENTS = {
    'queued': 'Tool session queued', 'running': 'Tool session started',
    'waiting': 'Tool session waiting', 'cancelling': 'Tool session stopping',
    'complete': 'Tool session completed', 'failed': 'Tool session failed',
    'cancelled': 'Tool session cancelled', 'interrupted': 'Tool session interrupted',
    'tool_started': 'Tool operation started', 'tool_complete': 'Tool operation completed',
}
LIMITATIONS = (
    'This view combines retained Work events, note and preference action states, and any configured scoped tool events.',
    'It does not collect chat transcripts, note contents, tool arguments, desktop process data, or credentials.',
    'History is limited by the existing source stores and this bounded recent window; it is not a complete audit archive.',
)


class ActivityError(ValueError):
    pass


def _identifier(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def _number(value, maximum=MAX_COUNT):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum


def _event_id(source, subject, discriminator):
    encoded = json.dumps([source, subject, discriminator], separators=(',', ':')).encode()
    return hashlib.sha256(encoded).hexdigest()


class ActivityFeed:
    def __init__(self, workflows=None, approvals=None, tool_events=None, clock=time.time):
        self.callbacks = {'workflows': workflows, 'approvals': approvals, 'tools': tool_events}
        self.clock = clock

    def snapshot(self, limit=DEFAULT_ITEMS):
        if type(limit) is not int or not 1 <= limit <= MAX_ITEMS:
            raise ActivityError('Choose a bounded activity limit.')
        now = self.clock()
        if not _number(now):
            raise ActivityError('The activity clock is unavailable.')
        items, sources, truncated = [], {}, False

        def append(source, subject, discriminator, kind, label, at, **metadata):
            if not _identifier(subject) or not _number(at) or at > now + MAX_CLOCK_SKEW_S:
                return
            items.append(dict(id=_event_id(source, subject, discriminator), source=source,
                subject_id=subject, kind=kind, label=label, at=at, **metadata))

        for source, callback in self.callbacks.items():
            if callback is None:
                sources[source] = 'not_configured'
                continue
            try:
                value = callback()
                if source == 'workflows':
                    rows = value.get('tasks', value.get('runs')) if isinstance(value, dict) else None
                    if not isinstance(rows, list):
                        raise ValueError
                    truncated |= len(rows) > MAX_WORKFLOWS
                    for run in rows[:MAX_WORKFLOWS]:
                        if (not isinstance(run, dict) or not _identifier(run.get('id'))
                                or run.get('status') not in STATUSES or not _number(run.get('created_at'))):
                            continue
                        metadata = {'status': run['status']}
                        if run.get('workflow') in WORKFLOWS:
                            metadata['workflow'] = run['workflow']
                        if run.get('phase') in PHASES:
                            metadata['phase'] = run['phase']
                        if type(run.get('total_tokens')) is int and _number(run['total_tokens']):
                            metadata['total_tokens'] = run['total_tokens']
                            metadata['usage_complete'] = run.get('usage_complete') is True
                        events = run.get('events', [])
                        if not isinstance(events, list):
                            events = []
                        truncated |= len(events) > MAX_EVENTS_PER_RUN
                        valid = False
                        for event in events[-MAX_EVENTS_PER_RUN:]:
                            if (not isinstance(event, dict) or event.get('kind') not in WORKFLOW_EVENTS
                                    or type(event.get('id')) is not int or not 1 <= event['id'] <= MAX_EVENT_SEQUENCE
                                    or not _number(event.get('elapsed_s'))):
                                continue
                            at = run['created_at'] + event['elapsed_s']
                            append('workflows', run['id'], event['id'], event['kind'],
                                WORKFLOW_EVENTS[event['kind']], at, **metadata)
                            valid = True
                        if not valid:
                            append('workflows', run['id'], ['status', run['status']], 'status',
                                'Work status: ' + run['status'], run['created_at'], **metadata)
                elif source == 'approvals':
                    rows = value.get('items') if isinstance(value, dict) else None
                    if not isinstance(rows, list):
                        raise ValueError
                    truncated |= len(rows) > MAX_APPROVALS
                    for action in rows[:MAX_APPROVALS]:
                        if (not isinstance(action, dict) or not _identifier(action.get('id'))
                                or action.get('status') not in APPROVAL_STATUSES):
                            continue
                        subject, status = action['id'], action['status']
                        preference = action.get('kind') == 'preferences'
                        prefix = 'preference_' if preference else ''
                        labels = PREFERENCE_EVENTS if preference else APPROVAL_EVENTS
                        append('approvals', subject, prefix + 'proposed', prefix + 'proposed',
                            labels['proposed'] if preference else 'Note edit proposed', action.get('created_at'), status=status)
                        if _number(action.get('decided_at')):
                            decision = 'rejected' if status == 'rejected' else 'approved'
                            append('approvals', subject, prefix + decision, prefix + decision,
                                labels[decision] if preference else 'Note edit rejected' if decision == 'rejected' else 'Note edit approved',
                                action['decided_at'], status=status)
                        if _number(action.get('applied_at')):
                            append('approvals', subject, prefix + 'applied', prefix + 'applied',
                                labels['applied'], action['applied_at'], status=status)
                        if _number(action.get('undone_at')):
                            append('approvals', subject, prefix + 'undone', prefix + 'undone',
                                labels['undone'], action['undone_at'], status=status)
                        if status not in ('applied', 'rejected', 'undone'):
                            append('approvals', subject, prefix + status, prefix + status,
                                labels[status], action.get('updated_at'), status=status)
                else:
                    if not isinstance(value, list):
                        raise ValueError
                    truncated |= len(value) > MAX_TOOL_EVENTS
                    for event in value[-MAX_TOOL_EVENTS:]:
                        if (not isinstance(event, dict) or event.get('kind') not in TOOL_EVENTS
                                or type(event.get('id')) is not int or not 1 <= event['id'] <= MAX_EVENT_SEQUENCE):
                            continue
                        append('tools', event.get('run_id'), event['id'], event['kind'],
                            TOOL_EVENTS[event['kind']], event.get('at'))
                sources[source] = 'available'
            except Exception:
                # A failing provider may mention a path or token in its error.
                sources[source] = 'unavailable'
        unique = {item['id']: item for item in items}
        ordered = sorted(unique.values(), key=lambda item: (item['at'], item['id']), reverse=True)
        return {'schema_version': SCHEMA_VERSION, 'generated_at': now, 'items': ordered[:limit],
            'truncated': truncated or len(ordered) > limit, 'sources': sources,
            'limitations': list(LIMITATIONS)}
