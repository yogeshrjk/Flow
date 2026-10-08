"""Intelligent correction: classify A-F, respect passive/balanced/active. Rule-based pre-pass;
LLM does final phrasing. Never embarrass, max 1 correction per turn unless active."""
import re
from dataclasses import dataclass


@dataclass
class Mistake:
    kind: str  # past_tense, article, preposition, didnt_went, since_for, ...
    category: str  # A minor, B important, C meaning-changing, D pronunciation, E repeated, F naturalness
    pattern: str
    correction_hint: str


RULES: list[tuple[str, str, str, str]] = [
    # (kind, regex, category, hint)
    ("didnt_went", r"\bdidn'?t\s+(went|saw|came|ate|did|had|took|made)\b", "B",
     "After 'didn't', use base form: \"didn't go\" not \"didn't went\"."),
    ("have_went", r"\b(have|has)\s+(went|saw|ate|came|took)\b", "B",
     "Use past participle with have/has, or simple past: \"I went\" / \"I have gone\"."),
    ("yesterday_go", r"\byesterday\s+i\s+(go|come|eat|work|do)\b", "B",
     "Yesterday needs past tense: \"Yesterday I went ...\"."),
    ("i_go_yesterday", r"\bi\s+(go|work)\s+(to\s+)?(office|market|school)\s+yesterday\b", "B",
     "Past time → past tense: \"I went to the office yesterday\"."),
    ("from_last_years", r"\bfrom\s+last\s+\d+\s+years?\b", "B",
     "Say \"for five years\" / \"since 2020\": \"I've been working here for five years\"."),
    ("am_working_from", r"\bi\s+am\s+working\s+(from|since)\b", "B",
     "For duration use present perfect continuous: \"I've been working ... for ...\"."),
    ("went_office", r"\bwent\s+office\b", "A",
     "Needs a preposition/article: \"went to the office\"."),
    ("discuss_about", r"\bdiscuss\s+about\b", "F",
     "Natural collocation is \"discuss something\" (no 'about')."),
    ("do_decision", r"\bdo\s+a\s+decision\b", "F",
     "Collocation: \"make a decision\" not \"do a decision\"."),
    ("very_good_overuse", r"\bvery\s+good\b", "F",
     "Try 'great / solid / impressive / really good' for variety."),
]


def detect_mistakes(text: str, history_counts: dict | None = None) -> list[Mistake]:
    t = text.lower()
    out: list[Mistake] = []
    history_counts = history_counts or {}
    for kind, rx, cat, hint in RULES:
        m = re.search(rx, t)
        if m:
            category = cat
            # repeated -> promote to E
            if history_counts.get(kind, 0) >= 2:
                category = "E"
            out.append(Mistake(kind=kind, category=category, pattern=m.group(0), correction_hint=hint))
    return out


def should_correct(mistakes: list[Mistake], setting: str) -> list[Mistake]:
    """Filter by correction setting. Passive: only C/E (+B if meaning risk). Balanced: B/C/E. Active: all."""
    if not mistakes:
        return []
    if setting == "passive":
        keep = [m for m in mistakes if m.category in ("C", "E")]
        # allow one B if nothing else but clearly past-tense error
        if not keep:
            b = [m for m in mistakes if m.category == "B" and m.kind in ("yesterday_go", "didnt_went", "have_went", "i_go_yesterday")]
            keep = b[:1]
        return keep[:1]
    if setting == "active":
        return mistakes[:2]
    # balanced
    keep = [m for m in mistakes if m.category in ("B", "C", "E")]
    if not keep and mistakes:
        # one naturalness tip occasionally
        keep = mistakes[:1] if mistakes[0].category == "F" else []
    return keep[:1]


def correction_instruction(mistakes: list[Mistake]) -> str:
    if not mistakes:
        return "No correction this turn. Just respond naturally with a follow-up question."
    bits = "; ".join(f"{m.pattern!r} → {m.correction_hint}" for m in mistakes)
    return (
        "If natural, include ONE brief correction using acknowledge→correct→why→continue. "
        f"Target: {bits}. Keep it to one sentence of correction, then continue conversing. "
        "Never embarrass. Never correct the same point twice in a row."
    )
