"""Session feedback limited to observed transcript and measured microphone evidence."""
from collections import Counter


def build_summary(session: dict, profile: dict) -> dict:
    users = [t for t in session.get("turns", []) if t.get("role") == "user"]
    words = sum(len(t.get("text", "").split()) for t in users)
    mistakes = session.get("mistake_counts", {})
    top = Counter(mistakes).most_common(1)
    focus = top[0][0].replace("_", " ") if top else "longer, connected answers"
    phrase = "I've been working on..." if "past" in focus else "For example, ..."
    return {"duration_min": round(session.get("duration_s", 0) / 60, 1),
            "speaking_min": round(session.get("speaking_seconds", 0) / 60, 2),
            "words_spoken": words, "turns": len(users),
            "fluency": "Needs repeated audio assessment", "grammar": f"{sum(mistakes.values())} detected patterns",
            "vocabulary": "See observed vocabulary in daily progress", "main_improvement": focus,
            "useful_phrase": phrase, "pronunciation_focus": "Requires audio assessment; transcript matching is not pronunciation scoring",
            "next_goal": "Give three complete answers and review one recurring mistake.",
            "spoken_feedback": f"You practiced {words} words across {len(users)} turns. Next, work on {focus}. Try using '{phrase}' in a complete answer.",
            "level": session.get("level", "B1"), "assessment_note": "Provisional transcript-based estimate, not a CEFR certificate."}
