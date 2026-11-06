# Fuzzy plate search that tolerates OCR mistakes.
# Series and number are compared separately by edit distance; look-alike characters (บ/ป, 3/8) cost less and '?'
# matches anything. A part only one side has is skipped. Several plates can be searched, comma-separated.
import re
from functools import lru_cache

# Characters OCR confuses with each other on blurred or small plates
LOOKALIKE_GROUPS = [
    "ขฃชซ", "คดตฅ", "บปษ", "ผฝพฟ", "ถภฎฏ", "ทฑ", "มฆ", "รธ", "อฮ", "ลสศ", "ญฌณ",
    "08", "38", "68", "17", "56",
    # Read differently on the same motorcycle in different runs over input/IMG_9122.MOV
    "รชซ", "ษฐศ", "ฐม", "ฐอ", "สฆ", "ซว", "ขย", "ษย", "ฑถ", "28", "46",
]
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


def _pair_costs() -> dict:
    # {(a, b): cost} of every pair of plate characters costing less than 1 (see normalize()); a table, so
    # the edit distance below looks a pair up instead of calling a function for each one.
    chars = "0123456789" + "".join(map(chr, range(ord("ก"), ord("ฮ") + 1)))
    costs = {(c, c): 0.0 for c in chars + UNKNOWN}
    for c in chars:
        costs[c, UNKNOWN] = costs[UNKNOWN, c] = UNKNOWN_COST
    for group in LOOKALIKE_GROUPS:
        costs.update({(a, b): LOOKALIKE_COST for a in group for b in group if a != b})
    return costs


_PAIR_COSTS = _pair_costs()


def normalize(text: str) -> str:
    # Plate characters only: Thai consonants, digits and '?' (spaces, dashes and vowels dropped).
    return re.sub(r"[^0-9ก-ฮ?]", "", str(text).translate(_THAI_DIGITS))


def normalize_query(text: str) -> str:
    # What the user typed, as plate characters; '?' is not a wildcard there, so it is dropped too.
    return normalize(text).replace(UNKNOWN, "")


def split_queries(text: str) -> list:
    # The plates in a search, split at commas, without blanks or repeats: '1กข987, 2847,' -> ['1กข987', '2847'].
    return list(dict.fromkeys(part.strip() for part in str(text).split(",") if part.strip()))


@lru_cache(maxsize=200_000)
def split_plate(text: str) -> tuple:
    # (series, number), normalized: '6ขน 690' -> ('6ขน', '690'), '6มก65' -> ('6มก', '65'),
    # '7793' -> ('', '7793'). Stored plates are 'series number'; typed ones may lack the space.
    # Cached: the same stored plates are split again on every search.
    parts = str(text).split()
    if len(parts) == 2:
        return normalize(parts[0]), normalize(parts[1])
    series, number = re.fullmatch(r"(.*[ก-ฮ])?([0-9?]*)", normalize(text)).groups()
    return series or "", number


def _distance(query: str, plate: str, partial: bool) -> float:
    # Weighted edit distance. partial: the query may start and end anywhere in the plate.
    # The cost of a character pair is symmetric, so the two may be passed either way round.
    cost = _PAIR_COSTS.get
    prev = [0.0] * (len(plate) + 1) if partial else [float(j) for j in range(len(plate) + 1)]
    for i, q in enumerate(query, 1):
        row = [float(i)]
        for j, p in enumerate(plate, 1):
            row.append(min(prev[j] + 1.0,                     # query character missing from the plate
                           row[j - 1] + 1.0,                  # extra character in the plate
                           prev[j - 1] + cost((q, p), 1.0)))  # same, look-alike or different character
        prev = row
    return min(prev) if partial else prev[-1]


@lru_cache(maxsize=200_000)
def _part_similarity(a: str, b: str, partial: bool) -> float:
    # 0..1 for one part of a plate. partial: the shorter one may match anywhere inside the longer one.
    # Cached: many plates share their series or their number, so each pair is compared once.
    score = 1.0 - _distance(a, b, partial=False) / max(len(a), len(b))
    if partial and len(a) != len(b):
        short, long = sorted((a, b), key=len)
        score = max(score, (1.0 - _distance(short, long, partial=True) / len(short)) * PARTIAL_WEIGHT)
    return max(0.0, score)


def similarity(query: tuple, plate: tuple) -> float:
    # 0..1, how likely `plate` is the plate the user typed; both are (series, number) from split_plate().
    # The series may be typed in part ('ขน' for '6ขน'); the number must match as a whole ('65' is not '650').
    (q_series, q_number), (series, number) = query, plate
    total = weight = 0.0
    if q_series and series:
        total += _part_similarity(q_series, series, True) * len(q_series)
        weight += len(q_series)
    if q_number and number:
        total += _part_similarity(q_number, number, False) * len(q_number)
        weight += len(q_number)
    if not weight:
        return 0.0
    score = total / weight
    if bool(q_series) != bool(series) or bool(q_number) != bool(number):
        score *= PARTIAL_WEIGHT
    return score


def group_plates(counts: dict, min_score: float) -> list:
    # Groups whole plates that are likely one motorcycle read differently by OCR, each group headed by its
    # most seen spelling. counts is {plate: times seen}; a plate with '?' only joins a group.
    heads, groups = [], []
    for plate in sorted(counts, key=lambda p: (-counts[p], p)):
        parts = split_plate(plate)
        if not all(parts):
            continue
        match = next((i for i, head in enumerate(heads) if similarity(head, parts) >= min_score), None)
        if match is not None:
            groups[match].append(plate)
        elif UNKNOWN not in plate:
            heads.append(parts)
            groups.append([plate])
    return groups


def rank_plates(query: str, plates, min_score: float) -> dict:
    # {plate: score} of the plates scoring at least min_score. query may hold several plates separated by
    # commas; each plate scores its best match against any of them.
    queries = [tuple(part.replace(UNKNOWN, "") for part in split_plate(q)) for q in split_queries(query)]
    scores = {}
    for plate in plates:
        parts = split_plate(plate)
        score = max(similarity(q, parts) for q in queries)
        if score >= min_score:
            scores[plate] = score
    return scores
