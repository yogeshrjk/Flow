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
    replacement: str = ""


RULES: list[tuple[str, str, str, str]] = [
    # (kind, regex, category, hint)
    ("didnt_went", r"\b(did|didn'?t|did\s+not)\s+(went|saw|came|ate|did|had|took|made|knew|spoke|wrote|bought|gave|told|found|heard|thought|understood|brought|drove|felt|kept|left|ran|met|said|sent|spent|slept|broke|drank)\b", "B",
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
    ("could_went", r"\b(could|would|should|can|might)\s+(went|saw|ate|came|took|done)\b", "B",
     "Modals (could/would/should) are followed by the base verb: 'could go' not 'could went'."),
    ("should_to", r"\b(should|could|must|can)\s+to\s+\w+\b", "B",
     "Do not put 'to' after modal verbs: say 'I should go' not 'I should to go'."),
    ("why_you_are", r"\bwhy\s+(you\s+are|he\s+is|they\s+are)\b", "B",
     "In questions, invert subject and auxiliary: 'Why are you' not 'Why you are'."),
    ("listen_music", r"\blisten\s+music\b", "A",
     "Use preposition 'to' with listen: 'listen to music'."),
    ("he_dont", r"\b(he|she|it)\s+don'?t\b", "B",
     "With he/she/it, use 'doesn\'t': 'he doesn\'t' not 'he don\'t'."),
    ("i_am_agree", r"\bi\s+(am|'m)\s+agree\b", "B",
     "Say 'I agree' instead of 'I am agree'."),
    ("he_have", r"\b(he|she|it)\s+have\b", "B",
     "Use 'has' with he/she/it: 'he has' not 'he have'."),
    ("they_has", r"\b(they|we)\s+has\b", "B",
     "Use 'have' with they/we: 'they have' not 'they has'."),
    ("they_was", r"\b(they|we|you)\s+was\b", "B",
     "Use 'were' with they/we/you: 'they were' not 'they was'."),
    ("more_better", r"\bmore\s+(better|easier|faster|taller|smarter)\b", "F",
     "Do not use 'more' with comparative adjectives."),
    ("peoples", r"\bpeoples\b", "B",
     "'People' is already plural: say 'people' not 'peoples'."),
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
            matched_pat = m.group(0)
            repl = get_replacement_for_mistake(kind, matched_pat)
            out.append(Mistake(kind=kind, category=category, pattern=matched_pat, correction_hint=hint, replacement=repl))
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


def correction_instruction(mistakes: list[Mistake], mode: str = "free") -> str:
    if not mistakes:
        return "No grammar correction needed. Respond naturally and keep the conversation flowing smoothly."
    bits = "; ".join(f"{m.pattern!r} → {m.correction_hint}" for m in mistakes)
    if mode == "free":
        return (
            f"If natural, seamlessly model the correct phrasing ({bits}) in your reply without lecturing, "
            "pointing out the error, or saying 'you made a mistake'. Keep the chat relaxed and casual."
        )
    return (
        f"Gently weave in the natural correction ({bits}) in one clear, friendly sentence, "
        "then naturally continue the conversation. Never embarrass and avoid canned praise."
    )

PAST_TO_BASE = {
    "went": "go", "saw": "see", "came": "come", "ate": "eat",
    "did": "do", "had": "have", "took": "take", "made": "make", "done": "do",
    "knew": "know", "spoke": "speak", "wrote": "write", "bought": "buy",
    "gave": "give", "told": "tell", "found": "find", "heard": "hear",
    "thought": "think", "understood": "understand", "brought": "bring",
    "drove": "drive", "felt": "feel", "kept": "keep", "left": "leave",
    "ran": "run", "met": "meet", "said": "say", "sent": "send",
    "spent": "spend", "slept": "sleep", "broke": "break", "drank": "drink",
}
PAST_TO_PARTICIPLE = {
    "went": "gone", "saw": "seen", "came": "come", "ate": "eaten", "took": "taken",
    "wrote": "written", "spoke": "spoken", "gave": "given", "broke": "broken",
    "drank": "drunk", "did": "done",
}


def get_replacement_for_mistake(kind: str, pattern: str) -> str:
    pat_lower = pattern.lower().strip()
    if kind == "didnt_went":
        for past, base in PAST_TO_BASE.items():
            if past in pat_lower:
                return re.sub(rf"\b{past}\b", base, pat_lower)
        return pat_lower
    elif kind == "have_went":
        for past, part in PAST_TO_PARTICIPLE.items():
            if past in pat_lower:
                return re.sub(rf"\b{past}\b", part, pat_lower)
        return pat_lower
    elif kind == "could_went":
        for past, base in PAST_TO_BASE.items():
            if past in pat_lower:
                return re.sub(rf"\b{past}\b", base, pat_lower)
        return pat_lower
    elif kind == "yesterday_go":
        repl = {"go": "went", "come": "came", "eat": "ate", "work": "worked", "do": "did"}
        for pres, past in repl.items():
            if re.search(rf"\bi\s+{pres}\b", pat_lower):
                return re.sub(rf"\bi\s+{pres}\b", f"I {past}", pat_lower)
        return pat_lower
    elif kind == "i_go_yesterday":
        repl = {"go": "went", "work": "worked"}
        for pres, past in repl.items():
            if re.search(rf"\bi\s+{pres}\b", pat_lower):
                return re.sub(rf"\bi\s+{pres}\b", f"I {past}", pat_lower)
        return pat_lower
    elif kind == "from_last_years":
        return re.sub(r"\bfrom\s+last\b", "for the last", pat_lower)
    elif kind == "am_working_from":
        return re.sub(r"\bi\s+am\s+working\s+(?:from|since)\b", "I have been working since", pat_lower)
    elif kind == "went_office":
        return re.sub(r"\bwent\s+office\b", "went to the office", pat_lower)
    elif kind == "discuss_about":
        return re.sub(r"\bdiscuss\s+about\b", "discuss", pat_lower)
    elif kind == "do_decision":
        return re.sub(r"\bdo\s+a\s+decision\b", "make a decision", pat_lower)
    elif kind == "should_to":
        return re.sub(r"\b(should|could|must|can)\s+to\s+", r"\1 ", pat_lower)
    elif kind == "why_you_are":
        if "you are" in pat_lower:
            return "why are you"
        elif "he is" in pat_lower:
            return "why is he"
        elif "they are" in pat_lower:
            return "why are they"
        return pat_lower
    elif kind == "listen_music":
        return "listen to music"
    elif kind == "he_dont":
        return re.sub(r"\bdon'?t\b", "doesn't", pat_lower)
    elif kind == "i_am_agree":
        return "I agree"
    elif kind == "he_have":
        return re.sub(r"\bhave\b", "has", pat_lower)
    elif kind == "they_has":
        return re.sub(r"\bhas\b", "have", pat_lower)
    elif kind == "they_was":
        return re.sub(r"\bwas\b", "were", pat_lower)
    elif kind == "more_better":
        return re.sub(r"\bmore\s+", "", pat_lower)
    elif kind == "peoples":
        return "people"
    return pat_lower


def apply_fixes_to_sentence(text: str, mistakes: list[Mistake]) -> str:
    """Apply replacement fixes to text and return the grammatically corrected sentence."""
    fixed = text
    for m in mistakes:
        if m.pattern and m.replacement and m.pattern.lower() != m.replacement.lower():
            fixed = re.sub(re.escape(m.pattern), m.replacement, fixed, flags=re.IGNORECASE)
    return fixed
