"""Small, validated speaking plans shared by the brain and local voice worker."""
import collections
import re
import threading

MAX_SPEECH_CHARS = 6000
MAX_EMPHASIS_CHARS = 60
MAX_EMPHASES = 2
PLAN_CACHE_SIZE = 128
LONG_REPLY_BYTES = 1000
NATIVE_TURN_BYTES = 360
STYLES = ('neutral', 'explaining', 'reassuring', 'curious', 'warning', 'pleased', 'dry', 'uncertain')
DELIVERY_SCHEMA = {'type': 'object', 'properties': {
    'style': {'type': 'string', 'enum': list(STYLES)},
    'emphasis': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': MAX_EMPHASES}},
    'required': ['style', 'emphasis'], 'additionalProperties': False}
DELIVERY_INSTRUCTIONS = '''
Include speech={style,emphasis} in your answer JSON. Choose how to say the answer
from its meaning and this conversation: neutral, explaining, reassuring, curious,
warning, pleased, dry, or uncertain. Preserve one composed British speaker.
Use restrained emotion, natural phrasing and contractions when appropriate.
Default to neutral. Warnings are firm, never shouted; reassurance is warm, never
theatrical. emphasis is at most two short, exact excerpts from answer deserving
subtle word stress (usually []). Do not put delivery tags in answer. Do not alter
facts, grounding or permissions to create a performance. This speaking plan is
metadata, not another message and not a tool request.
'''
BASE_DIRECTION = ("Preserve the reference speaker's British accent and vocal identity. "
    'Speak clearly with relaxed, connected phrasing and subtle natural word '
    'stress. Maintain a composed conversational delivery throughout.')
STYLE_CUES = {
    'neutral': '', 'explaining': '',
    'reassuring': 'calm, warmly reassuring delivery',
    'curious': 'calm, gently questioning delivery',
    'warning': 'calm, firm and clear delivery',
    'pleased': 'calm delivery with quiet warmth',
    'dry': 'calm delivery with subtle dry amusement',
    'uncertain': 'thoughtful, measured delivery',
}
ABBREVIATIONS = {'CPU': 'C P U', 'GPU': 'G P U', 'API': 'A P I',
                 'GHz': 'gigahertz', 'MHz': 'megahertz'}
ONES = ('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen '
        'fifteen sixteen seventeen eighteen nineteen').split()
TENS = 'twenty thirty forty fifty sixty seventy eighty ninety'.split()
SCALES = ((10**12, 'trillion'), (10**9, 'billion'), (10**6, 'million'), (1000, 'thousand'))
ORDINALS = {'one': 'first', 'two': 'second', 'three': 'third', 'five': 'fifth', 'eight': 'eighth',
            'nine': 'ninth', 'twelve': 'twelfth'}
MONTHS = ('January February March April May June July August September October November December').split()
CURRENCIES = {'£': ('pound', 'pounds', 'penny', 'pence'), '$': ('dollar', 'dollars', 'cent', 'cents'),
              '€': ('euro', 'euros', 'cent', 'cents')}
MAGNITUDES = {'k': 'thousand', 'K': 'thousand', 'm': 'million', 'M': 'million', 'mn': 'million',
              'bn': 'billion', 'B': 'billion', 'tn': 'trillion', 'T': 'trillion'}
# Only units verified by rendering and transcription; others (e.g. km) the voice already reads well.
UNITS = {'kg': 'kilograms', 'mph': 'miles per hour', 'KB': 'kilobytes', 'MB': 'megabytes',
         'GB': 'gigabytes', 'TB': 'terabytes', 'GHz': 'gigahertz', 'MHz': 'megahertz'}
_plans = collections.OrderedDict()
_lock = threading.RLock()


def number_words(value):
    """British cardinal words for a non-negative integer: 1250 -> one thousand two hundred and fifty."""
    value = int(value)
    if value < 20:
        return ONES[value]
    if value < 100:
        return TENS[value // 10 - 2] + ('-' + ONES[value % 10] if value % 10 else '')
    if value < 1000:
        rest = value % 100
        return ONES[value // 100] + ' hundred' + (' and ' + number_words(rest) if rest else '')
    for size, name in SCALES:
        if value >= size:
            rest = value % size
            joiner = ' and ' if 0 < rest < 100 else ' '
            return number_words(value // size) + ' ' + name + (joiner + number_words(rest) if rest else '')
    return str(value)


def ordinal_words(value):
    words = number_words(value)
    head, _, last = words.rpartition(' ') if ' ' in words else ('', '', words)
    stem, dash, tail = last.rpartition('-')
    word = tail or last
    word = ORDINALS.get(word) or (word[:-1] + 'ieth' if word.endswith('y') else word + 'th')
    return (head + ' ' if head else '') + (stem + dash if dash else '') + word


def year_words(value):
    value = int(value)
    if 2000 <= value < 2010 or value % 1000 == 0:
        return number_words(value)
    high, low = divmod(value, 100)
    return number_words(high) + ' ' + ('hundred' if low == 0 else ('oh ' + ONES[low] if low < 10 else number_words(low)))


def decimal_words(text):
    whole, _, fraction = text.replace(',', '').partition('.')
    words = number_words(whole or 0)
    return words + (' point ' + ' '.join(ONES[int(d)] for d in fraction) if fraction else '')


def _money(match):
    symbol, amount, magnitude = match.group(1), match.group(2).replace(',', ''), match.group(3)
    one, many, minor_one, minor_many = CURRENCIES[symbol]
    if magnitude:
        return decimal_words(amount) + ' ' + MAGNITUDES[magnitude] + ' ' + many
    whole, _, pence = amount.partition('.')
    minor = int(pence.ljust(2, '0')[:2]) if pence else 0
    if not int(whole or 0) and minor:
        return number_words(minor) + ' ' + (minor_one if minor == 1 else minor_many)
    words = number_words(whole) + ' ' + (one if int(whole) == 1 else many)
    if pence and int(pence.ljust(2, '0')[:2]):
        minor = int(pence.ljust(2, '0')[:2])
        words = (words + ' ' + number_words(minor)) if symbol == '£' else (words + ' and ' + number_words(minor) + ' ' + (minor_one if minor == 1 else minor_many))
    return words


def normalize_for_speech(text):
    """Only patterns the voice was measured to misread: currency, grouped thousands,
    ISO dates and decimals before units. Times, ordinals and plain numbers are left
    to the voice, which already reads them correctly."""
    text = re.sub(r'(?<![\w.])([£$€])\s?(\d[\d,]*(?:\.\d+)?)(?:\s?(bn|mn|tn|[kKmMBT])\b)?', _money, text)
    text = re.sub(r'\b(\d{4})-(\d{2})-(\d{2})\b', lambda m: (
        'the ' + ordinal_words(int(m.group(3))) + ' of ' + MONTHS[int(m.group(2)) - 1] + ' ' + year_words(m.group(1))
        if 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31 else m.group(0)), text)
    text = re.sub(r'\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b', lambda m: decimal_words(m.group(0)), text)
    units = '|'.join(re.escape(unit) for unit in sorted(UNITS, key=len, reverse=True))
    text = re.sub(r'\b(\d+(?:\.\d+)?)\s?(' + units + r')(?![\w/])', lambda m: decimal_words(m.group(1)) + ' ' + UNITS[m.group(2)], text)
    return text


def spoken_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_SPEECH_CHARS:
        raise ValueError('Enter a shorter, non-empty reply for speech.')
    text = re.sub(r'```[\s\S]*?```', ' The code is shown on screen. ', text)
    text = re.sub(r'\[([^\]]+)\]\([^\s)]+\)', r'\1', text)
    text = re.sub(r'https?://\S+', 'the link shown on screen', text)
    text = re.sub(r'<\|[^>]*\|>|<[^>]+>', '', text)
    text = re.sub(r'^\s*(?:#{1,6}\s+|[-*+]\s+|\d+\.\s+)', '', text, flags=re.M)
    text = re.sub(r'\[\d+(?:[,\s]+\d+)*\]', '', text)
    text = text.replace('**', '').replace('__', '').replace('`', '')
    # User/model text cannot inject Fish speaker or expression tags.
    text = text.replace('[', '').replace(']', '')
    text = ''.join(ch if ch.isprintable() or ch.isspace() else ' ' for ch in text)
    text = text.replace('°C', ' degrees Celsius').replace('%', ' per cent')
    text = normalize_for_speech(text)
    for abbreviation, pronunciation in ABBREVIATIONS.items():
        text = re.sub(r'\b' + re.escape(abbreviation) + r'\b', pronunciation, text)
    text = ' '.join(text.split())
    if not text:
        raise ValueError('There are no words to speak.')
    return text


def delivery_for(text, value=None):
    """Only a known style and literal emphasis excerpts reach the synthesizer."""
    fallback = 'curious' if text.rstrip().endswith('?') else 'neutral'
    if not isinstance(value, dict):
        return {'style': fallback, 'emphasis': []}
    style = value.get('style', fallback)
    if style not in STYLES:
        style = fallback
    emphasis = value.get('emphasis', [])
    if not isinstance(emphasis, list):
        emphasis = []
    clean = []
    for part in emphasis:
        if (isinstance(part, str) and part.strip() and len(part) <= MAX_EMPHASIS_CHARS
                and part in text and not re.search(r'[\[\]<>\r\n]', part)
                and part not in clean):
            clean.append(part)
    return {'style': style, 'emphasis': clean[:MAX_EMPHASES]}


def remember_delivery(answer):
    if not isinstance(answer, dict) or not isinstance(answer.get('answer'), str) or not answer['answer'].strip():
        return answer
    try:
        text = spoken_text(answer['answer'])
    except ValueError:
        return answer
    plan = delivery_for(text, answer.get('speech'))
    with _lock:
        _plans[text] = plan
        _plans.move_to_end(text)
        while len(_plans) > PLAN_CACHE_SIZE:
            _plans.popitem(last=False)
    return answer


def speaking_plan(text, hint=None):
    with _lock:
        recorded = _plans.get(text)
    # Current reply metadata travels with each client. The small registry also
    # supports legacy/static callers; it must not overwrite newer reply context.
    return delivery_for(text, hint if isinstance(hint, dict) else recorded)


def synthesis_text(text, plan):
    text = spoken_text(text)
    plan = delivery_for(text, plan)
    for part in sorted(plan['emphasis'], key=len, reverse=True):
        text = text.replace(part, '[emphasis] ' + part, 1)
    direction = STYLE_CUES[plan['style']]
    cue = '[' + direction + '] ' if direction else ''
    if len((cue + text).encode()) <= LONG_REPLY_BYTES:
        return '<|speaker:0|>' + cue + text
    turns, current = [], ''
    for sentence in re.split(r'(?<=[.!?;])\s+', text):
        for word in sentence.split():
            if len(word.encode()) > NATIVE_TURN_BYTES:
                raise ValueError('A word is too long to speak clearly.')
            candidate = (current + ' ' + word).strip()
            if current and len(candidate.encode()) > NATIVE_TURN_BYTES:
                turns.append(current)
                current = word
            else:
                current = candidate
        if current:
            turns.append(current)
            current = ''
    return '\n'.join('<|speaker:0|>' + (cue if index == 0 else '') + part
                     for index, part in enumerate(turns))
