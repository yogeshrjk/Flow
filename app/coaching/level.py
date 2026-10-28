"""CEFR estimation A1-C2 (heuristic, continuous). Never announced immediately."""
import re

WORDS_A1 = set("i you he she we they go come eat work school office day good bad happy tired".split())

def estimate_level(text: str, prev: str = "B1") -> str:
    words = re.findall(r"[a-zA-Z']+", text.lower())
    if not words:
        return prev
    order = ["A1", "A2", "B1", "B2", "C1", "C2"]
    uniq = len(set(words))
    avg_len = sum(len(w) for w in words) / max(1, len(words))
    n = len(words)
    long_words = sum(1 for w in words if len(w) > 7)
    subordinate = len(re.findall(r"\b(because|although|however|while|since|which|who|despite|unless)\b", text.lower()))
    idiom = len(re.findall(r"\b(phrasal|breakthrough|trade-?off|on the other hand|as well as)\b", text.lower()))
    score = 2  # B1 baseline index
    if n <= 4 and uniq <= 4:
        score -= 1
    if n >= 25 or uniq >= 18:
        score += 1
    if avg_len > 5.2 or long_words >= 3:
        score += 1
    if subordinate >= 1:
        score += 1
    if idiom >= 1 or (subordinate >= 2 and n > 30):
        score += 1
    if avg_len < 3.8 and n < 8:
        score -= 1
    # Hindi ratio pushes down slightly (needs support, not punishment)
    hindi = len(re.findall(r"[\u0900-\u097F]", text))
    if hindi > 5:
        score -= 1
    score = max(0, min(5, score))
    # smooth: move at most 1 step from prev
    pi = order.index(prev) if prev in order else 2
    if score > pi:
        score = pi + 1
    elif score < pi:
        score = pi - 1
    return order[score]


def level_to_setting(level: str, setting: str) -> str:
    """Map UI setting (auto/beginner/...) to prompt hint key."""
    s = (setting or "auto").lower()
    mapping = {"auto": "auto", "beginner": "beginner", "elementary": "elementary",
               "intermediate": "intermediate", "upper": "upper", "upper intermediate": "upper",
               "advanced": "advanced"}
    return mapping.get(s, "auto")
