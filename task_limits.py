"""Shared top-level task admission settings; no model or service side effects."""
import os
import re

DEFAULT_TASK_MAX_CONCURRENT = 2
MAX_TASK_MAX_CONCURRENT = 8
MIN_TASK_MAX_CONCURRENT = 1
TASK_LIMIT_VARIABLE = 'TASK_MAX_CONCURRENT'
INVALID_TASK_LIMIT_NOTICE = 'Invalid TASK_MAX_CONCURRENT; using one task until corrected.'


def parse_task_limit(value):
    """Reject booleans, signs, whitespace, fractions and unbounded values."""
    if (type(value) is int and MIN_TASK_MAX_CONCURRENT <= value <= MAX_TASK_MAX_CONCURRENT):
        return value
    if isinstance(value, str) and re.fullmatch(r'[1-9][0-9]*', value):
        number = int(value)
        if MIN_TASK_MAX_CONCURRENT <= number <= MAX_TASK_MAX_CONCURRENT:
            return number
    raise ValueError('TASK_MAX_CONCURRENT must be an integer from 1 to 8.')


def read_task_limit(environment=None):
    environment = os.environ if environment is None else environment
    if TASK_LIMIT_VARIABLE not in environment:
        return DEFAULT_TASK_MAX_CONCURRENT, None
    try:
        return parse_task_limit(environment[TASK_LIMIT_VARIABLE]), None
    except ValueError:
        return MIN_TASK_MAX_CONCURRENT, INVALID_TASK_LIMIT_NOTICE


TASK_MAX_CONCURRENT, TASK_LIMIT_NOTICE = read_task_limit()
