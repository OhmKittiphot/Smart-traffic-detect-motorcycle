from collections import Counter, defaultdict

# Stands for a character that was read but is not valid at its place; it keeps the line's length
# so reads of the same plate still line up character by character when they are voted on
UNKNOWN = "?"


def vote_line(reads):
    # Votes one plate line over (text, score, weight) reads: the length with the most weight wins, then each
    # position takes its most-weighted character. Returns (text, confidence); UNKNOWN where no read was valid.
    reads = [(text, score, weight) for text, score, weight in reads if text and weight > 0]
    if not reads:
        return "", 0.0

    length_weight = Counter()
    for text, _, weight in reads:
        length_weight[len(text)] += weight
    length = max(length_weight, key=length_weight.get)
    same_length = [(text, score, weight) for text, score, weight in reads if len(text) == length]

    chars, agreement = [], []
    for i in range(length):
        votes = defaultdict(float)
        for text, _, weight in same_length:
            if text[i] != UNKNOWN:
                votes[text[i]] += weight
        if not votes:
            chars.append(UNKNOWN)
            agreement.append(0.0)
            continue
        best = max(votes, key=votes.get)
        chars.append(best)
        agreement.append(votes[best] / sum(weight for _, _, weight in same_length))

    best_score = max(score for _, score, _ in same_length)
    return "".join(chars), sum(agreement) / length * best_score


def vote_temporal_strings(candidates):
    # Pick the whole string read most often over time (ties go to the higher total confidence).
    counts = Counter()
    confidence = Counter()
    for text, score in candidates:
        text = str(text or "").strip()
        if text:
            counts[text] += 1
            confidence[text] += max(float(score), 0.0)

    if not counts:
        return ""
    return max(counts, key=lambda text: (counts[text], confidence[text]))
