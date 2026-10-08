"""Per-user profile persistence (JSON files, free). Tracks mistakes/vocab/topics — no sensitive data."""
import json
import os
import time
from pathlib import Path


def _root(data_dir: str) -> Path:
    p = Path(data_dir) / "profiles"
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_profile(data_dir: str, user_id: str = "default") -> dict:
    fp = _root(data_dir) / f"{user_id}.json"
    if fp.exists():
        try:
            return json.loads(fp.read_text())
        except Exception:
            pass
    return {
        "user_id": user_id,
        "level": "B1",
        "grammar": {"past_tense": 0, "articles": 0, "prepositions": 0, "didnt_went": 0},
        "vocabulary": {"overused": {}, "learned": []},
        "pronunciation": {},
        "topics": [],
        "sessions": 0,
        "minutes_spoken": 0.0,
        "words_spoken": 0,
        "streak": 0,
        "last_session": None,
        "confidence_avg": 0.5,
    }


def save_profile(data_dir: str, profile: dict) -> None:
    fp = _root(data_dir) / f"{profile.get('user_id', 'default')}.json"
    fp.write_text(json.dumps(profile, indent=2, ensure_ascii=False))


def update_after_turn(profile: dict, mistakes: list, words: int, seconds: float, conf: float, topic_hint: str = "") -> dict:
    from app.coaching.correction import Mistake
    for m in mistakes:
        mm: Mistake = m
        # map kind -> grammar bucket
        if mm.kind in ("yesterday_go", "i_go_yesterday", "have_went", "didnt_went", "from_last_years", "am_working_from"):
            profile["grammar"]["past_tense"] = profile["grammar"].get("past_tense", 0) + 1
        elif "article" in mm.kind:
            profile["grammar"]["articles"] = profile["grammar"].get("articles", 0) + 1
        else:
            profile["grammar"][mm.kind] = profile["grammar"].get(mm.kind, 0) + 1
    profile["words_spoken"] = profile.get("words_spoken", 0) + words
    profile["minutes_spoken"] = round(profile.get("minutes_spoken", 0.0) + seconds / 60.0, 2)
    # running avg confidence
    prev = profile.get("confidence_avg", 0.5)
    profile["confidence_avg"] = round((prev + conf) / 2, 3)
    if topic_hint and topic_hint not in profile.get("topics", []):
        profile["topics"] = (profile.get("topics", []) + [topic_hint])[-20:]
    return profile


def record_session(profile: dict) -> dict:
    import datetime
    today = datetime.date.today().isoformat()
    last = profile.get("last_session")
    if last == today:
        pass
    elif last is None:
        profile["streak"] = 1
    else:
        try:
            d1 = datetime.date.fromisoformat(last)
            d2 = datetime.date.fromisoformat(today)
            profile["streak"] = profile.get("streak", 0) + 1 if (d2 - d1).days == 1 else 1
        except Exception:
            profile["streak"] = 1
    profile["last_session"] = today
    profile["sessions"] = profile.get("sessions", 0) + 1
    return profile
