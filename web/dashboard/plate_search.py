"""Fuzzy licence plate search: finds a plate even where OCR misread or missed some of it.

A plate is split into its series ('6ขน') and its number ('690'), and each part is compared on its own with
an edit distance in which swapping two characters that look alike on a plate (บ/ป, ด/ต, 3/8 ...) costs
less than swapping two different ones; a '?' stored in a plate (a character OCR could not read) matches
anything at a small cost. A part only one side has is left out, because OCR often reads the number line
alone: '6มก65' finds '65', and '690' finds '6ขน 690'.

Several plates can be searched at once, separated by commas ('1กข987, 2847'); a stored plate then scores
its best match against any of them.
"""
import re

# Characters OCR confuses with each other on blurred or small plates
LOOKALIKE_GROUPS = [
    "ขฃชซ", "คดตฅ", "บปษ", "ผฝพฟ", "ถภฎฏ", "ทฑ", "มฆ", "รธ", "อฮ", "ลสศ", "ญฌณ",
    "08", "38", "68", "17", "56",
]
_LOOKALIKES = {frozenset((a, b)) for group in LOOKALIKE_GROUPS for a in group for b in group if a != b}

UNKNOWN = "?"
# Edit costs: an unrelated character (or one missing / extra) costs 1
LOOKALIKE_COST = 0.35
UNKNOWN_COST = 0.25
# A match on part of a plate (a part missing on one side, or a series typed only in part)
# ranks below a match on the whole plate
PARTIAL_WEIGHT = 0.9
# Shorter queries would match nearly every plate
MIN_QUERY_LENGTH = 2
# Plates searched at once (comma-separated)
MAX_QUERIES = 10

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def normalize(text: str) -> str:
    """Plate characters only: Thai consonants, digits and '?' (spaces, dashes and vowels dropped)."""
    return re.sub(r"[^0-9ก-ฮ?]", "", str(text).translate(_THAI_DIGITS))


def normalize_query(text: str) -> str:
    """What the user typed, as plate characters; '?' is not a wildcard there, so it is dropped too."""
    return normalize(text).replace(UNKNOWN, "")


def split_queries(text: str) -> list:
    """The plates in a search, split at commas, without blanks or repeats: '1กข987, 2847,' -> ['1กข987', '2847']."""
    return list(dict.fromkeys(part.strip() for part in str(text).split(",") if part.strip()))


def split_plate(text: str) -> tuple:
    """(series, number), normalized: '6ขน 690' -> ('6ขน', '690'), '6มก65' -> ('6มก', '65'),
    '7793' -> ('', '7793'). Stored plates are 'series number'; typed ones may lack the space."""
    parts = str(text).split()
    if len(parts) == 2:
        return normalize(parts[0]), normalize(parts[1])
    series, number = re.fullmatch(r"(.*[ก-ฮ])?([0-9?]*)", normalize(text)).groups()
    return series or "", number


def _char_cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    if a == UNKNOWN or b == UNKNOWN:
        return UNKNOWN_COST
    return LOOKALIKE_COST if frozenset((a, b)) in _LOOKALIKES else 1.0


def _distance(query: str, plate: str, partial: bool) -> float:
    """Weighted edit distance. partial: the query may start and end anywhere in the plate.
    The cost of a character pair is symmetric, so the two may be passed either way round."""
    prev = [0.0] * (len(plate) + 1) if partial else [float(j) for j in range(len(plate) + 1)]
    for i, q in enumerate(query, 1):
        row = [float(i)]
        for j, p in enumerate(plate, 1):
            row.append(min(prev[j] + 1.0,                      # query character missing from the plate
                           row[j - 1] + 1.0,                   # extra character in the plate
                           prev[j - 1] + _char_cost(q, p)))    # same, look-alike or different character
        prev = row
    return min(prev) if partial else prev[-1]


def _part_similarity(a: str, b: str, partial: bool) -> float:
    """0..1 for one part of a plate. partial: the shorter one may match anywhere inside the longer one."""
    score = 1.0 - _distance(a, b, partial=False) / max(len(a), len(b))
    if partial and len(a) != len(b):
        short, long = sorted((a, b), key=len)
        score = max(score, (1.0 - _distance(short, long, partial=True) / len(short)) * PARTIAL_WEIGHT)
    return max(0.0, score)


def similarity(query: tuple, plate: tuple) -> float:
    """0..1, how likely `plate` is the plate the user typed; both are (series, number) from split_plate().
    The series may be typed in part ('ขน' for '6ขน'); the number must match as a whole ('65' is not '650')."""
    total = weight = 0.0
    for q, p, partial in ((query[0], plate[0], True), (query[1], plate[1], False)):
        if q and p:
            total += _part_similarity(q, p, partial) * len(q)
            weight += len(q)
    if not weight:
        return 0.0
    score = total / weight
    if any(bool(q) != bool(p) for q, p in zip(query, plate)):
        score *= PARTIAL_WEIGHT
    return score


def rank_plates(query: str, plates, min_score: float) -> dict:
    """{plate: score} of the plates scoring at least min_score. query may hold several plates separated by
    commas; each plate scores its best match against any of them."""
    queries = [tuple(part.replace(UNKNOWN, "") for part in split_plate(q)) for q in split_queries(query)]
    scores = {}
    for plate in plates:
        parts = split_plate(plate)
        score = max(similarity(q, parts) for q in queries)
        if score >= min_score:
            scores[plate] = score
    return scores
