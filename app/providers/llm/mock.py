"""Offline mock LLM (free, unlimited). Natural voice-chat replies for demo mode.
Used when GEMINI_API_KEY is empty so the app runs with zero cost.
Also used in tests."""
import asyncio
import random
import re
from typing import AsyncGenerator
from app.providers.llm.base import LLMProvider

FOLLOWUPS = [
    "Nice! What kind of project were you working on?",
    "Oh, interesting — what's the hardest part of it?",
    "Wait, seriously? You built that yourself?",
    "Okay, I get what you mean. What happened next?",
    "That sounds like a solid day. What did you enjoy most?",
]

CORRECTION_SNIPPETS = {
    "yesterday_go": "By the way, one small thing — since you're talking about yesterday, say 'I went to the office' instead of 'I go to office'.",
    "i_go_yesterday": "Quick note: for yesterday use past tense — 'I went to the office yesterday'.",
    "didnt_went": "Small tip: after \"didn't\" use the base form — \"didn't go\" not \"didn't went\".",
    "have_went": "Almost — say 'I went there yesterday' (simple past for finished time).",
    "from_last_years": "One small improvement: say 'I've been working as a developer for five years'.",
    "am_working_from": "Try 'I've been working here for five years' — that's the natural form for duration.",
}


class MockProvider(LLMProvider):
    name = "mock-free"

    def __init__(self):
        self.turn = 0

    async def stream(self, messages: list[dict], max_tokens: int = 250) -> AsyncGenerator[str, None]:
        text = self.reply(messages)
        # stream word-by-word to exercise chunker/audio queue
        for w in text.split(" "):
            yield w + " "
            await asyncio.sleep(0.01)

    def reply(self, messages: list[dict]) -> str:
        user_texts = [m["content"] for m in messages if m.get("role") == "user"]
        last = user_texts[-1] if user_texts else ""
        self.turn = len(user_texts)
        low = last.lower()

        # Acceptance-test path: tender management system etc.
        if "tender management" in low or "tender" in low:
            return "Oh, interesting. What's the hardest part of it?"
        if re.search(r"\bi go to office\b", low) or ("office" in low and "project" in low):
            return "Nice. What are you working on?"
        if "how's your day" in low or not last:
            return "Hey! How's your day going?"

        # delayed pattern correction every ~3rd turn if mistake present
        for key, snippet in CORRECTION_SNIPPETS.items():
            if key in low or key.replace("_", " ") in low:
                if self.turn >= 3 and self.turn % 3 == 0:
                    return f"Yeah, I get what you mean. {snippet} But overall, you're communicating your ideas clearly. What else did you work on?"
                break
        # Hindi/Hinglish support
        if re.search(r"[\u0900-\u097F]", last) or "mujhe" in low or "thoda" in low:
            return "That's okay. Try saying: 'I find it a little difficult to explain.' Now say the whole sentence yourself — what were you trying to say?"
        # interview
        if "interview" in low:
            return "Okay, let's start. Tell me about yourself."
        if len(last.split()) <= 3:
            return random.choice(["Got it. Can you tell me a little more?", "Okay — and then what happened?"])
        return random.choice(FOLLOWUPS)
