"""Roleplay scenarios + daily challenges (deterministic, free, no LLM needed)."""
import datetime

SCENARIOS = {
    "job_interview": {
        "title": "Job Interview",
        "opener": "Hey! Today we're practicing a Job Interview together. Take your time, relax, and when you're ready, tell me a little about yourself!",
        "role": "the hiring manager / job interviewer",
        "desc": "A professional job interview. Act as the interviewer asking structured questions about experience, skills, strengths, and background. Stay strictly on the interview topic.",
    },
    "meeting": {
        "title": "Team Meeting",
        "opener": "Hey! We're practicing a Team Standup Meeting today. Relax, speak naturally, and share what you worked on yesterday!",
        "role": "the team lead / colleague in a daily standup",
        "desc": "A workplace team standup meeting. Discuss tasks, sprint updates, blockers, and project progress.",
    },
    "presentation": {
        "title": "Presentation",
        "opener": "Welcome! We're practicing a Project Pitch Presentation today. When you're ready, pitch your idea in a minute or two!",
        "role": "an executive audience member / stakeholder",
        "desc": "A business presentation or project pitch. Ask insightful questions about their pitch, value proposition, and timeline.",
    },
    "client_call": {
        "title": "Client Call",
        "opener": "Hi! We're practicing a Client Discussion Call today. Let's do this step-by-step — how is our project timeline looking?",
        "role": "the client on a project call",
        "desc": "A professional client call. Discuss deliverables, project requirements, milestones, and feedback.",
    },
    "restaurant": {
        "title": "Restaurant",
        "opener": "Hey there! We're practicing ordering at a Restaurant today. Imagine I'm your server — what can I get started for you today?",
        "role": "the restaurant waiter / server",
        "desc": "A restaurant dining experience. Take orders, suggest dishes, answer questions about the menu, and handle dining requests.",
    },
    "airport": {
        "title": "Airport",
        "opener": "Hello! Today we're practicing Airport Check-In. Let's practice comfortably — where are you flying today?",
        "role": "the airline check-in / gate agent",
        "desc": "Airport check-in and boarding. Inquire about destination, baggage, travel documents, and boarding gates.",
    },
    "hotel": {
        "title": "Hotel",
        "opener": "Welcome! We're practicing Hotel Check-In today. Let's practice at your pace — do you have a reservation with us?",
        "role": "the hotel front desk concierge",
        "desc": "Hotel check-in and guest services. Assist with reservations, room keys, hotel amenities, and guest inquiries.",
    },
    "networking": {
        "title": "Networking",
        "opener": "Hey there! We're practicing Networking conversation today. What kind of work do you do?",
        "role": "a fellow professional at a networking event",
        "desc": "A professional networking mixer. Ask about their work, industry, current projects, and exchange ideas.",
    },
    "casual": {
        "title": "Casual Conversation",
        "opener": "Hey! How's your day going? Let's chat comfortably about whatever is on your mind.",
        "role": "a friendly conversational partner",
        "desc": "A casual, relaxed conversation about daily life, hobbies, weekends, and interests.",
    },
    "technical": {
        "title": "Technical Interview",
        "opener": "Welcome! We're practicing a Technical Software Interview today in a friendly, relaxed way. When you're ready, how would you approach designing a URL shortener?",
        "role": "the senior tech interviewer",
        "desc": "A technical software engineering interview. Ask architecture, system design, coding trade-offs, and data flow questions.",
    },
    "salary": {
        "title": "Salary Negotiation",
        "opener": "Hey! We're practicing a Salary Negotiation discussion today. Take your time — what are your compensation expectations for this role?",
        "role": "the HR compensation manager",
        "desc": "A salary and compensation negotiation. Discuss compensation expectations, benefits package, and bonus structure.",
    },
    "help": {
        "title": "Asking for Help",
        "opener": "Hey! We're practicing Asking for Help at work today. Feel free to explain what problem or blocker you're facing!",
        "role": "a supportive senior colleague / mentor",
        "desc": "Assisting a colleague with a problem. Guide them through troubleshooting, debugging, or workflow issues.",
    },
    "complaint": {
        "title": "Making a Complaint",
        "opener": "Hello! We're practicing Making a Customer Service Complaint today. Don't worry, explain clearly what issue happened with your order!",
        "role": "a customer service manager",
        "desc": "Handling a customer complaint. Listen attentively, empathize, investigate the issue, and propose a resolution.",
    },
    "doctor": {
        "title": "Doctor Appointment",
        "opener": "Hi! We're practicing a Doctor Appointment consultation today. Take your time — what symptoms are you experiencing today?",
        "role": "the physician / doctor",
        "desc": "A medical clinic visit. Inquire about symptoms, onset, medical history, and provide guidance.",
    },
    "phone": {
        "title": "Phone Call",
        "opener": "Hello! We're practicing a Professional Phone Call today. Hi there, is this a good time to speak for a couple of minutes?",
        "role": "the person on the phone call",
        "desc": "A focused phone call. Speak in a clear, natural phone conversational style.",
    },
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
