"""Answer explicit background-status questions from canonical task facts."""
import re

QUESTIONS = frozenset({'what are you working on', 'what tasks are running',
    'what is running', 'background task status', 'workflow status',
    'are any tasks running', 'are you working on anything'})
TERMINAL = frozenset({'complete', 'failed', 'cancelled', 'interrupted'})


def answer(question, task_api):
    normalized = re.sub(r'\s+', ' ', question.strip().lower()).rstrip('?.!')
    normalized = re.sub(r'^(?:hey\s+)?jarvis[, ]+', '', normalized)
    if normalized not in QUESTIONS:
        return None
    catalog = task_api.catalog()
    if not catalog['enabled']:
        text = 'Background workflows are disabled in this build, Sir.'
    elif not catalog['available']:
        text = "I can't read background workflow status right now, Sir. Open Work for details."
    else:
        owned = set(catalog['active_ids'])
        active = [task for task in catalog['tasks'] if task['id'] in owned]
        running = sum(task['status'] not in TERMINAL for task in active)
        cleaning = len(active) - running
        if not active:
            text = 'No background workflows are running, Sir.'
        else:
            parts = []
            if running:
                parts.append(f'{running} background workflow' + ('s are' if running != 1 else ' is') + ' running')
            if cleaning:
                parts.append(f'{cleaning} workflow' + ('s are' if cleaning != 1 else ' is') + ' finishing cleanup')
            text = '; '.join(parts).capitalize() + ', Sir. Open Work for progress and saved results.'
    return {'answer': text, 'about_notes': False, 'nodes': []}
