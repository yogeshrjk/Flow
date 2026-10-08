"""Confidence + difficulty ramp. Signals: length, hesitation, idk-rate, hindi-switch."""
import re


def confidence_score(text: str) -> float:
    t = text.lower()
    words = re.findall(r"[a-zA-Z']+", t)
    n = len(words)
    if n == 0:
        return 0.1
    score = 0.5
    score += min(0.3, n / 100)  # longer -> more confident
    hes = len(re.findall(r"\b(uh|um|hmm|err|like,+|you know)\b", t)) + text.count("...")
    score -= min(0.25, hes * 0.05)
    idk = len(re.findall(r"\bi don'?t know\b", t))
    score -= min(0.3, idk * 0.15)
    hindi = len(re.findall(r"[\u0900-\u097F]", text))
    if hindi:
        score -= 0.1
    q = text.count("?")
    score += min(0.1, q * 0.03)  # asking questions = engagement
    return round(max(0.0, min(1.0, score)), 2)


EASY = ["What did you do today?", "What did you have for breakfast?", "What are you working on?"]
MED = ["Tell me about a difficult decision you made recently.", "Describe your room without translating.", "Explain how you make tea."]
HARD = ["Do you think AI will replace software developers?", "Should companies allow full remote work? Argue both sides."]


def next_challenge_prompt(avg_conf: float, session_count: int) -> str:
    if session_count < 1 or avg_conf < 0.4:
        return EASY[session_count % len(EASY)]
    if avg_conf < 0.65:
        return MED[session_count % len(MED)]
    return HARD[session_count % len(HARD)]
