from collections import Counter
import re


def _iter_candidates(candidates):
    if candidates is None:
        return []

    normalized = []
    for item in candidates:
        if isinstance(item, str):
            text = item
            score = 1.0
        elif isinstance(item, tuple) and len(item) >= 2:
            text, score = item[0], item[1]
        else:
            continue

        text = str(text or "").strip()
        if not text:
            continue

        try:
            score = float(score)
        except Exception:
            score = 1.0

        normalized.append((text, score))
    return normalized


def _normalize_vote_text(text):
    text = str(text or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", "", text)


def vote_characters_by_position(candidates):
    """Choose the most frequent character at each index across OCR outputs."""
    normalized = _iter_candidates(candidates)
    if not normalized:
        return ""

    strings = [
        (_normalize_vote_text(text), score)
        for text, score in normalized
        if _normalize_vote_text(text)
    ]
    if not strings:
        return ""

    max_len = max(len(text) for text, _ in strings)
    if max_len == 0:
        return ""

    vote_chars = []
    for idx in range(max_len):
        counter = Counter()
        weight = Counter()

        for text, conf in strings:
            if idx >= len(text):
                continue
            ch = text[idx]
            counter[ch] += 1
            weight[ch] += max(conf, 0.0)

        if not counter:
            continue

        best_char = max(
            counter.items(),
            key=lambda kv: (kv[1], weight[kv[0]], kv[0])
        )[0]
        vote_chars.append(best_char)

    return "".join(vote_chars)


def vote_temporal_strings(candidates):
    """Vote on a whole OCR string over time, preserving the full province chunk."""
    normalized = _iter_candidates(candidates)
    if not normalized:
        return ""

    counts = Counter()
    confidence = Counter()
    for text, score in normalized:
        key = str(text or "").strip()
        if not key:
            continue
        counts[key] += 1
        confidence[key] += max(float(score), 0.0)

    if not counts:
        return ""

    winner, _ = max(counts.items(), key=lambda kv: (kv[1], confidence[kv[0]], kv[0]))
    return winner
