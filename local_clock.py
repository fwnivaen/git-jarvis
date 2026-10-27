"""Deterministic answers to explicit local clock questions; no model call."""
from datetime import datetime
import re
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo('America/New_York')
LOCATION = 'New York'
TIME_QUESTIONS = frozenset({
    'what time is it', "what's the time", 'what is the time',
    'what is the current time', 'tell me the time', 'current time',
    'what time is it in new york', 'what is the time in new york',
})
DATE_QUESTIONS = frozenset({
    "what's today's date", 'what is todays date', "what is today's date",
    'what is the date today', 'what date is it', "what's the date", 'what is the date',
    'what day is it', 'what day is it today',
})


def answer(question, at=None):
    normalized = re.sub(r'\s+', ' ', question.strip().lower().replace('’', "'")).rstrip('?.!')
    normalized = re.sub(r'^(?:hey\s+)?jarvis[, ]+|^please\s+', '', normalized)
    normalized = re.sub(r'[, ]+(?:please|sir)$', '', normalized)
    if normalized not in TIME_QUESTIONS | DATE_QUESTIONS:
        return None
    if at is not None and (at.tzinfo is None or at.utcoffset() is None):
        raise ValueError('Clock fixture must include a timezone.')
    local = datetime.now(TIMEZONE) if at is None else at.astimezone(TIMEZONE)
    if normalized in TIME_QUESTIONS:
        text = f"It's {local.strftime('%I:%M %p').lstrip('0')} in {LOCATION}, Sir."
    else:
        text = f"It's {local.strftime('%A, %B')} {local.day}, {local.year}, Sir."
    return {'answer': text, 'about_notes': False, 'nodes': []}
