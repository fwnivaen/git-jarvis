"""Audio-anchored reading progress for the local voice. Standard library only.

The synthesizer gives no word timestamps. Real pauses in the generated audio
are matched (monotonic dynamic programming) to the reply's punctuation, and
words inside each phrase are spread over its spoken time by length. Anchors are
(audio seconds, fraction of the spoken text). Clients hold at the last anchor
rather than guessing ahead, so text follows the voice and never leads it.
"""
import re

FRAME_S = 0.01
SILENCE_DB_BELOW_SPEECH = 24.0
MIN_PAUSE_S = 0.12
UNSETTLED_TAIL_S = 0.8          # a pause this close to the audio end may still be growing
MAX_ANCHORS = 400
RECALIBRATE_FRACTION = 0.1
REACHED_SLACK = 0.03
TRAIL_FACTOR = 1.15            # unconfirmed speech is paced slightly slow so text trails rather than leads
SKIP_BOUNDARY_COST = {'.': 1.6, '!': 1.6, '?': 1.6, ';': 1.1, ':': 1.1, ',': 0.7, '-': 0.5}
EXTRA_PAUSE_COST = 1.4
BASE_TOLERANCE_S = 0.6
TOLERANCE_PER_S = 0.1


def pauses(levels_db, frame_s=FRAME_S):
    """levels_db: per-frame loudness. Returns (onset, end, [(start, stop), ...]) in seconds."""
    if not levels_db:
        return None
    ordered = sorted(levels_db)
    speech = ordered[int(len(ordered) * 0.9)]
    quiet = [level < speech - SILENCE_DB_BELOW_SPEECH for level in levels_db]
    voiced = [index for index, value in enumerate(quiet) if not value]
    if not voiced:
        return None
    first, last = voiced[0], voiced[-1]
    found, run = [], None
    for index in range(first, last + 1):
        if quiet[index]:
            run = index if run is None else run
        elif run is not None:
            if (index - run) * frame_s >= MIN_PAUSE_S:
                found.append((run * frame_s, index * frame_s))
            run = None
    return first * frame_s, (last + 1) * frame_s, found


def boundaries(text):
    """Phrase ends as (fraction of text, punctuation)."""
    total = max(1, len(text))
    return [(match.end() / total, match.group(1)[0])
            for match in re.finditer(r'([.!?;:,]+|\s[-–—])(?=\s|$)', text) if match.end() < len(text)]


def timeline(text, levels_db, estimate_s, complete, frame_s=FRAME_S):
    """Anchors [(seconds, fraction)] for the audio heard so far, monotonic in both."""
    found = pauses(levels_db, frame_s)
    if not found:
        return []
    onset, end, gaps = found
    heard = len(levels_db) * frame_s
    if not complete:
        gaps = [gap for gap in gaps if gap[1] < heard - UNSETTLED_TAIL_S]
    marks = boundaries(text)
    # Speaking time per unit of text: actual when complete, else the estimate.
    paused = sum(stop - start for start, stop in gaps)
    span = (end - onset - paused) if complete else max(estimate_s - onset - paused, end - onset - paused)
    span = max(span, 1e-3)

    pause_before = [0.0]
    for start, stop in gaps:
        pause_before.append(pause_before[-1] + stop - start)
    rows, cols = len(marks), len(gaps)

    def match(span):
        # cost[i][j]: best with first i boundaries and first j pauses considered.
        infinite = float('inf')
        cost = [[infinite] * (cols + 1) for _ in range(rows + 1)]
        back = [[None] * (cols + 1) for _ in range(rows + 1)]
        cost[0][0] = 0.0
        for i in range(rows + 1):
            for j in range(cols + 1):
                here = cost[i][j]
                if here == infinite:
                    continue
                skip = here + SKIP_BOUNDARY_COST.get(marks[i][1], 0.7) if i < rows else infinite
                if skip < cost[i + 1][j] if i < rows else False:
                    cost[i + 1][j] = skip; back[i + 1][j] = (i, j, False)
                if j < cols and here + EXTRA_PAUSE_COST < cost[i][j + 1]:
                    cost[i][j + 1] = here + EXTRA_PAUSE_COST; back[i][j + 1] = (i, j, False)
                if i < rows and j < cols:
                    guess = onset + marks[i][0] * span + pause_before[j]
                    tolerance = BASE_TOLERANCE_S + TOLERANCE_PER_S * guess
                    step = here + min(3.0, abs(guess - gaps[j][0]) / tolerance)
                    if step < cost[i + 1][j + 1]:
                        cost[i + 1][j + 1] = step; back[i + 1][j + 1] = (i, j, True)
        # Unfinished replies: the remaining boundaries may simply not be spoken yet.
        if complete:
            i, j = rows, cols
        else:
            # Punctuation the audio heard so far must already have passed still
            # counts as skipped, so the match cannot settle a phrase early.
            reached = (end - onset - pause_before[cols]) / span
            owed = [0.0] * (rows + 1)
            for r in range(rows - 1, -1, -1):
                owed[r] = owed[r + 1] + (SKIP_BOUNDARY_COST.get(marks[r][1], 0.7) if marks[r][0] < reached - REACHED_SLACK else 0.0)
            i, j = min(((r, cols) for r in range(rows + 1)), key=lambda key: cost[key[0]][key[1]] + owed[key[0]])
        found = []
        while back[i][j] is not None:
            pi, pj, paired = back[i][j]
            if paired:
                found.append((marks[pi][0], pj))
            i, j = pi, pj
        return found[::-1]

    matched = match(span)
    if not complete and matched and matched[-1][0] >= RECALIBRATE_FRACTION:
        # Re-match with the speaking rate measured from this reply's own pauses.
        fraction, index = matched[-1]
        span = max(1e-3, (gaps[index][0] - onset - pause_before[index]) / fraction)
        matched = match(span)
    matched = [(fraction, gaps[index]) for fraction, index in matched]
    anchors = [(onset, 0.0)]
    for fraction, (start, stop) in matched:
        if fraction > anchors[-1][1] and start > anchors[-1][0]:
            anchors += [(start, fraction), (stop, fraction)]
    if complete:
        anchors.append((max(end, anchors[-1][0] + frame_s), 1.0))
    else:
        # Speech after the last settled pause is paced by the deliberately slow
        # length estimate, so an unconfirmed phrase trails the voice, never leads it.
        last_time, last_fraction = anchors[-1]
        final_word = max((match.start() for match in re.finditer(r'\S+', text)), default=0) / max(1, len(text))
        reach = min(final_word, last_fraction + max(0.0, end - last_time) / (span * TRAIL_FACTOR))
        if end > last_time and reach > last_fraction:
            anchors.append((end, reach))
    return [(round(seconds, 3), round(fraction, 4)) for seconds, fraction in anchors[:MAX_ANCHORS]]


def fraction_at(anchors, seconds, complete):
    """Spoken fraction at a playback time. Holds at the last anchor until more is known."""
    if not anchors or seconds <= anchors[0][0]:
        return 0.0
    for (t0, f0), (t1, f1) in zip(anchors, anchors[1:]):
        if seconds <= t1:
            return f0 + (f1 - f0) * (seconds - t0) / max(t1 - t0, 1e-6)
    return 1.0 if complete else anchors[-1][1]


HOLD_MATCH_S = 0.4              # an aligned end this close to a detected pause start is that pause
ALIGNED_MIN_MATCH = 0.8         # share of aligner words that must map onto the text, else the result is discarded


def _norm(word):
    return re.sub(r'[^0-9a-z]', '', word.lower())


def forced_anchors(text, items, prefix_chars=None, start_chars=0):
    """Word-level anchors from a forced aligner's [(word, start, end)] over text[start_chars:prefix_chars]."""
    limit = len(text) if prefix_chars is None else prefix_chars
    words = [(start_chars + match.start(), start_chars + match.end(), _norm(match.group()))
             for match in re.finditer(r'\S+', text[start_chars:limit])]
    words = [word for word in words if word[2]]
    total = max(1, len(text))
    anchors, cursor, matched, usable = [], 0, 0, 0
    for word, start, end in items:
        key = _norm(word)
        if not key:
            continue
        usable += 1
        for index in range(cursor, min(cursor + 3, len(words))):       # tolerate a skipped token
            if words[index][2] == key or words[index][2].startswith(key) or key.startswith(words[index][2]):
                begin, finish = words[index][0] / total, words[index][1] / total
                if (not anchors or (start >= anchors[-1][0] and begin >= anchors[-1][1])) and end >= start:
                    anchors += [(start, begin), (end, finish)]
                    matched += 1
                cursor = index + 1
                break
    if not usable or matched / usable < ALIGNED_MIN_MATCH:
        return []
    return [(round(seconds, 3), round(fraction, 4)) for seconds, fraction in anchors]


def merge(forced, pauses, text='', heard_s=None, complete=False):
    """Exact word anchors first. Beyond them, progress continues only from the last aligned
    word at that reply's own measured pace (pauses included, then slowed by TRAIL_FACTOR),
    never from the separate pause match, which can disagree and jump ahead."""
    if not forced:
        return pauses
    if complete or heard_s is None:
        return forced
    last_time, last_fraction = forced[-1]
    pace = max(last_time - forced[0][0], 1e-3) / max(last_fraction - forced[0][1], 1e-3)   # seconds per text fraction
    # The aligned part ends at a settled pause: hold through it; speech resumes when it ends.
    hold = [(t1, f1) for (t0, f0), (t1, f1) in zip(pauses, pauses[1:])
            if f0 == f1 and t1 > t0 and abs(t0 - last_time) <= HOLD_MATCH_S and t1 > last_time]
    if hold:
        forced = forced + [(hold[0][0], last_fraction)]
        last_time = hold[0][0]
    final_word = max((match.start() for match in re.finditer(r'\S+', text)), default=0) / max(1, len(text))
    reach = min(final_word, last_fraction + max(0.0, heard_s - last_time) / (pace * TRAIL_FACTOR))
    return forced + ([(round(heard_s, 3), round(reach, 4))] if heard_s > last_time and reach > last_fraction else [])


def settled_phrase(anchors):
    """The last confirmed phrase end in a pause timeline: (pause start seconds, text fraction)."""
    for (t0, f0), (t1, f1) in zip(reversed(anchors[:-1]), reversed(anchors[1:])):
        if f0 == f1 and t1 > t0 and f0 > 0:
            return t0, f0
    return None
