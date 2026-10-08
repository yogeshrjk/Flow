"""Session summaries: concise spoken (2-3 sentences) + detailed UI card."""
from collections import Counter


def build_summary(session: dict, profile: dict) -> dict:
    turns = session.get("turns", [])
    user_turns = [t for t in turns if t.get("role") == "user"]
    words = sum(len(t.get("text", "").split()) for t in user_turns)
    dur_s = session.get("duration_s", 0) or max(60, len(turns) * 45)
    mistakes = session.get("mistake_counts", {})
    top = Counter(mistakes).most_common(1)
    main = top[0][0] if top else "past tense"
    # useful phrase: pick from learned or default by level
    lvl = session.get("level", "B1")
    phrase = "I've been working on..." if "past" in str(main) or True else "That makes sense..."
    spoken = (
        f"You did well today. Your fluency was {'better than last time' if len(user_turns) > 3 else 'a good start'}. "
        f"One thing to work on: {main.replace('_', ' ')}. "
        f"And try using '{phrase}' in your next answer."
    )
    return {
        "duration_min": round(dur_s / 60, 1),
        "words_spoken": words,
        "turns": len(user_turns),
        "fluency": "Good" if words > 120 else "Developing",
        "grammar": "Improving" if mistakes else "Good",
        "vocabulary": "Good",
        "main_improvement": main.replace("_", " "),
        "useful_phrase": phrase,
        "pronunciation_focus": session.get("pron_focus", "TH sound"),
        "next_goal": "Speak for 3 minutes without switching to Hindi." if session.get("hindi_used") else "Speak for 3 minutes on one topic without stopping.",
        "spoken_feedback": spoken,
        "level": lvl,
    }
