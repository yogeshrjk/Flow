"""Roleplay scenarios + daily challenges (deterministic, free, no LLM needed)."""
import datetime

SCENARIOS = {
    "job_interview": {"title": "Job Interview", "opener": "Okay, let's start. Tell me about yourself."},
    "meeting": {"title": "Team Meeting", "opener": "Alright, quick standup — what did you work on yesterday?"},
    "presentation": {"title": "Presentation", "opener": "You've got the floor. Pitch your project in one minute."},
    "client_call": {"title": "Client Call", "opener": "Hi! Thanks for joining. Can you walk me through the timeline?"},
    "restaurant": {"title": "Restaurant", "opener": "Hi there! Table for one? What would you like to order?"},
    "airport": {"title": "Airport", "opener": "Hello! Where are you flying today? Can I see your booking?"},
    "hotel": {"title": "Hotel", "opener": "Welcome! Do you have a reservation with us?"},
    "networking": {"title": "Networking", "opener": "Hey! What do you do? Always curious meeting new folks here."},
    "casual": {"title": "Casual Conversation", "opener": "Hey! How's your day going?"},
    "technical": {"title": "Technical Interview", "opener": "Let's dive in — how would you design a URL shortener?"},
    "salary": {"title": "Salary Negotiation", "opener": "So, what are your compensation expectations for this role?"},
    "help": {"title": "Asking for Help", "opener": "Sure, happy to help. What are you stuck on?"},
    "complaint": {"title": "Making a Complaint", "opener": "I'm sorry to hear that. Can you tell me what happened?"},
    "doctor": {"title": "Doctor Appointment", "opener": "Hi, what brings you in today? What symptoms do you have?"},
    "phone": {"title": "Phone Call", "opener": "Hello? Hi, is this a good time to talk for two minutes?"},
}

CHALLENGES = [
    "Talk for two minutes about a project you're proud of. Don't worry about grammar. Just keep speaking.",
    "Describe your morning routine in detail, step by step.",
    "Explain how you make tea, as if teaching a friend.",
    "Tell me what you can see around you right now.",
    "Talk about a difficult decision you made recently.",
    "Describe your favourite movie and why you like it.",
    "Explain your job to a 10-year-old.",
]


def today_challenge() -> dict:
    idx = datetime.date.today().toordinal() % len(CHALLENGES)
    return {"id": idx, "title": f"Daily Speaking Challenge #{idx+1}", "prompt": CHALLENGES[idx], "target_s": 120}
