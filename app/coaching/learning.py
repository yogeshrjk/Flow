"""Evidence-based progression shared by all five modules; no fluency guarantees."""
import re
from datetime import datetime, timedelta, timezone
from app.coaching.level import estimate_level

MODES = ("free", "reading", "training", "roleplay", "listening")
LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")
INTERVALS = (1, 3, 7, 14, 30)


def today():
    return datetime.now(timezone.utc).date()


def state(profile):
    data = profile.setdefault("learning", {})
    for key, value in {"days": {}, "samples": [], "reviews": [], "observed_words": [],
                       "assessment": {"status": "provisional", "level": profile.get("level", "B1"), "samples": 0},
                       "listening": {"correct": 0, "attempts": 0}}.items():
        data.setdefault(key, value)
    return data


def assess(profile, text):
    data = state(profile)
    words = re.findall(r"[a-z]+(?:'[a-z]+)?", text.lower())
    if len(words) < 10:
        return data["assessment"]
    if text[:1600] in data["samples"]:
        return data["assessment"]
    data["samples"] = (data["samples"] + [text[:1600]])[-6:]
    joined = " ".join(data["samples"])
    count = len(re.findall(r"[a-z]+", joined.lower()))
    enough = len(data["samples"]) >= 3 and count >= 100
    last = data.get("last_assessment_date")
    reassess = not last or (today() - datetime.fromisoformat(last).date()).days >= 7
    if enough and reassess:
        # Median sample evidence, at most one band per week, prevents aggregate-length inflation.
        prior = profile.get("level", "B1")
        candidates = sorted(LEVELS.index(estimate_level(sample, prior)) for sample in data["samples"])
        profile["level"] = LEVELS[candidates[len(candidates) // 2]]
        data["last_assessment_date"] = today().isoformat()
    data["assessment"] = {"status": "estimated" if enough else "provisional",
                          "level": profile.get("level", "B1"), "samples": len(data["samples"]),
                          "word_count": count, "method": "transcript heuristic; not a certified CEFR assessment"}
    return data["assessment"]


def record_practice(profile, mode, text, mistakes=(), seconds=None):
    data = state(profile)
    key = today().isoformat()
    day = data["days"].setdefault(key, {"words": 0, "speaking_seconds": 0, "turns": 0, "mistakes": 0, "modules": {}})
    words = re.findall(r"[a-z]+(?:'[a-z]+)?", text.lower())
    day["words"] += len(words)
    day["turns"] += 1
    day["mistakes"] += len(mistakes)
    day["speaking_seconds"] = round(day["speaking_seconds"] + max(0, min(180, seconds or 0)), 2)
    if mode in MODES:
        day["modules"][mode] = day["modules"].get(mode, 0) + 1
    data["days"] = dict(sorted(data["days"].items())[-180:])
    data["observed_words"] = sorted(set(data["observed_words"]) | set(words))[:5000]
    assess(profile, text)
    for mistake in mistakes:
        card_id = mistake.kind
        card = next((c for c in data["reviews"] if c["id"] == card_id), None)
        if not card:
            card = {"id": card_id, "prompt": mistake.correction_hint, "example": text[:240],
                    "stage": 0, "due": key, "occurrences": 0}
            data["reviews"].append(card)
        card["occurrences"] += 1
        card["due"] = min(card["due"], key)
    data["reviews"] = data["reviews"][-100:]


def review(profile, card_id, correct):
    card = next((c for c in state(profile)["reviews"] if c["id"] == card_id), None)
    if not card:
        raise ValueError("Unknown review card")
    if card.get("last_review") == today().isoformat():
        return card  # double clicks cannot jump the spaced-review schedule
    card["stage"] = min(len(INTERVALS), card["stage"] + 1) if correct else 0
    card["due"] = (today() + timedelta(days=INTERVALS[max(0, card["stage"] - 1)])).isoformat()
    card["last_review"] = today().isoformat()
    return card


def daily_plan(profile):
    data = state(profile)
    key = today().isoformat()
    due = [c for c in data["reviews"] if c["due"] <= key]
    weaknesses = sorted(profile.get("grammar", {}).items(), key=lambda pair: pair[1], reverse=True)
    focus = weaknesses[0][0].replace("_", " ") if weaknesses and weaknesses[0][1] else "complete sentences"
    day = data["days"].get(key, {})
    titles = {"free": "Describe your day in complete sentences", "reading": "Read a short passage twice",
              "training": "Practice " + focus, "roleplay": "Use today's skill in a real situation",
              "listening": "Listen, answer, then replay and shadow"}
    total = data["listening"]
    return {"date": key, "level": profile.get("level", "B1"), "assessment": data["assessment"],
            "focus": focus, "tasks": [{"mode": m, "title": titles[m], "minutes": 3,
              "completed": day.get("modules", {}).get(m, 0) >= (1 if m == "listening" else 3)} for m in MODES],
            "reviews": due[:5], "today": day, "history": [{"date": k, **v} for k, v in sorted(data["days"].items())[-14:]],
            "observed_vocabulary": len(data["observed_words"]),
            "comprehension": {**total, "accuracy": round(total["correct"] / total["attempts"], 2) if total["attempts"] else None},
            "milestone": "Complete three practice turns in each speaking module and one listening exercise.",
            "note": "Times are practice targets. Speaking time includes measured microphone input only. Vocabulary counts observed words, not proven mastery. Pronunciation needs audio evidence."}


# Curated comprehension items have deterministic answers; the LLM never grades its own invented score.
LISTENING = [
    {"id": "cafe", "band": 0, "text": "I'd like a small tea, please. No sugar. I'll take it with me.",
     "question": "What does the customer want?", "choices": ["A small tea without sugar to take away", "A large coffee with sugar", "A tea to drink inside"], "answer": 0,
     "explanation": "'Take it with me' means takeaway. The customer explicitly says no sugar."},
    {"id": "train", "band": 0, "text": "The train to Brighton leaves at ten thirty from platform four. Please arrive ten minutes early.",
     "question": "When should the traveller arrive?", "choices": ["10:30", "10:20", "10:40"], "answer": 1,
     "explanation": "Ten minutes before 10:30 is 10:20."},
    {"id": "meeting", "band": 1, "text": "We were going to meet on Tuesday, but Priya is away. Let's push it back to Thursday afternoon. Send the draft before lunch.",
     "question": "What changed?", "choices": ["The draft is cancelled", "The meeting is earlier", "The meeting is now on Thursday"], "answer": 2,
     "explanation": "'Push it back' means postpone. The draft is still needed before lunch."},
    {"id": "shop", "band": 1, "text": "The blue jacket is out of stock, but we can order it. It should be here by Friday, unless the delivery is delayed.",
     "question": "Is Friday delivery guaranteed?", "choices": ["Yes, absolutely", "No, it depends on delivery", "The jacket cannot be ordered"], "answer": 1,
     "explanation": "'Should' and 'unless' express an expectation with a condition, not a guarantee."},
    {"id": "proposal", "band": 2, "text": "Although the proposal looks promising, I'd hold off on approving it until we've checked the running costs. The initial savings could be offset by maintenance.",
     "question": "What is the speaker recommending?", "choices": ["Approve immediately", "Reject it permanently", "Check ongoing costs before deciding"], "answer": 2,
     "explanation": "'Hold off' means wait. Lower initial costs may be cancelled out by maintenance costs."},
    {"id": "feedback", "band": 2, "text": "Your presentation was clear. If anything, the conclusion could have been more concise. I wouldn't change the examples; they made the argument easier to follow.",
     "question": "Which part needs improvement?", "choices": ["The length of the conclusion", "All of the examples", "The clarity of the argument"], "answer": 0,
     "explanation": "The examples are praised. 'More concise' suggests a shorter conclusion."},
]


def listening_exercise(profile):
    data = state(profile)
    band = min(2, LEVELS.index(profile.get("level", "B1")) // 2)
    pool = [e for e in LISTENING if e["band"] == band]
    e = pool[data["listening"]["attempts"] % len(pool)]
    data["pending_listening"] = e["id"]
    return {k: v for k, v in e.items() if k not in ("answer", "explanation", "band")}


def grade_listening(profile, exercise_id, answer):
    data = state(profile)
    if data.get("pending_listening") != exercise_id:
        raise ValueError("Start a new listening exercise before answering")
    e = next((e for e in LISTENING if e["id"] == exercise_id), None)
    if e is None or not isinstance(answer, int) or not 0 <= answer < len(e["choices"]):
        raise ValueError("Choose one of the listed answers")
    correct = answer == e["answer"]
    data["listening"]["attempts"] += 1
    data["listening"]["correct"] += int(correct)
    data.pop("pending_listening", None)
    record_practice(profile, "listening", "", mistakes=())
    return {"correct": correct, "answer": e["answer"], "explanation": e["explanation"], "transcript": e["text"]}
