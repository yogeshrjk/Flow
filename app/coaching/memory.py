"""Validated, atomic local learner profiles. Transcript data is personal data."""
import copy
import json
import logging
import os
import re
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


def validate_id(value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid profile or session identifier")
    return value


def _root(data_dir: str) -> Path:
    p = Path(data_dir) / "profiles"
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_profile(data_dir: str, user_id: str = "default") -> dict:
    from app.coaching.training import get_default_training_profile
    validate_id(user_id)
    defaults = {
        "user_id": user_id, "level": "B1",
        "grammar": {"past_tense": 0, "articles": 0, "prepositions": 0, "didnt_went": 0},
        "vocabulary": {"overused": {}, "learned": []}, "pronunciation": {}, "topics": [],
        "sessions": 0, "minutes_spoken": 0.0, "words_spoken": 0, "streak": 0,
        "last_session": None, "confidence_avg": 0.5, "mode_summaries": {},
        "training": get_default_training_profile(user_id, "B1"),
    }
    fp = _root(data_dir) / f"{user_id}.json"
    try:
        stored = json.loads(fp.read_text(encoding="utf-8"))
        if not isinstance(stored, dict):
            raise ValueError("profile must be an object")
        for key, default in defaults.items():
            value = stored.get(key)
            if value is not None and isinstance(value, type(default)):
                defaults[key] = value
        # New optional learning fields are preserved on every read/write.
        for key in ("learning", "last_chat_summary"):
            if key in stored:
                defaults[key] = stored[key]
    except FileNotFoundError:
        pass
    except (ValueError, OSError):
        log.warning("Profile could not be read: %s", user_id)
    defaults["user_id"] = user_id
    return defaults


def atomic_json(fp: Path, value: dict) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".profile-", dir=fp.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, fp)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_profile(data_dir: str, profile: dict) -> None:
    uid = validate_id(profile.get("user_id", "default"))
    atomic_json(_root(data_dir) / f"{uid}.json", profile)


def update_after_turn(profile: dict, mistakes: list, words: int, seconds: float, conf: float, topic_hint: str = "") -> dict:
    grammar = profile.setdefault("grammar", {})
    for m in mistakes:
        kind = m.kind
        bucket = "past_tense" if kind in ("yesterday_go", "i_go_yesterday", "have_went", "didnt_went", "from_last_years", "am_working_from") else ("articles" if "article" in kind else kind)
        grammar[bucket] = grammar.get(bucket, 0) + 1
    profile["words_spoken"] = profile.get("words_spoken", 0) + max(0, words)
    profile["minutes_spoken"] = round(profile.get("minutes_spoken", 0.0) + max(0, seconds) / 60, 4)
    profile["confidence_avg"] = round((profile.get("confidence_avg", 0.5) + conf) / 2, 3)
    if topic_hint and topic_hint not in profile.get("topics", []):
        profile["topics"] = (profile.get("topics", []) + [topic_hint])[-20:]
    return profile


def record_session(profile: dict) -> dict:
    from datetime import date
    today = date.today()
    last = profile.get("last_session")
    if last != today.isoformat():
        try:
            consecutive = last and (today - date.fromisoformat(last)).days == 1
        except ValueError:
            consecutive = False
        profile["streak"] = profile.get("streak", 0) + 1 if consecutive else 1
    profile["last_session"] = today.isoformat()
    profile["sessions"] = profile.get("sessions", 0) + 1
    return profile
