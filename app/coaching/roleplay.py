"""Roleplay scenarios + daily challenges (deterministic, free, no LLM needed)."""
import datetime
import re

SCENARIOS = {
    "market_friend": {
        "title": "Old Friend at the Market",
        "opener": "Hey! Oh my goodness, is that really you? It's been ages! What are you doing here at the market today?",
        "role": "an old close friend whom you haven't seen in years and just bumped into at a bustling local market",
        "desc": "You are an old close friend who unexpectedly bumped into the user at a local market. Chat warmly and excitedly in character about what you've been up to, reminiscing, and catching up. Stay 100% in character as their friend.",
    },
    "cafe": {
        "title": "Ordering at a Café",
        "opener": "Hi there! Welcome to the café. What can I get started for you today?",
        "role": "the friendly barista / café server",
        "desc": "A cozy café setting. Take coffee and pastry orders, make recommendations, and chat pleasantly with the customer.",
    },
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


def detect_roleplay_scenario(text: str) -> dict | None:
    """Identify if the user selected or proposed a roleplay scenario."""
    if not text:
        return None
    t = text.lower().strip()

    # Old friend / market scenario (matches "I met a old friend on the market and AI will act as my friend", etc.)
    if any(k in t for k in ("friend", "market", "bumping into", "bumped into", "supermarket", "grocery")):
        sc = dict(SCENARIOS["market_friend"])
        sc["key"] = "market_friend"
        return sc

    # Café / restaurant / ordering
    if any(k in t for k in ("cafe", "café", "coffee", "restaurant", "order", "waiter", "barista", "menu")):
        sc = dict(SCENARIOS["cafe"])
        sc["key"] = "cafe"
        return sc

    # Job interview
    if any(k in t for k in ("interview", "job", "hiring", "interviewer")):
        sc = dict(SCENARIOS["job_interview"])
        sc["key"] = "job_interview"
        return sc

    # Numbered choices
    if re.search(r"\b(?:first(?:\s+one)?|number\s+(?:one|1)|option\s+1|#1|1)\b", t):
        sc = dict(SCENARIOS["market_friend"])
        sc["key"] = "market_friend"
        return sc
    if re.search(r"\b(?:second(?:\s+one)?|number\s+(?:two|2)|option\s+2|#2|2)\b", t):
        sc = dict(SCENARIOS["cafe"])
        sc["key"] = "cafe"
        return sc
    if re.search(r"\b(?:third(?:\s+one)?|number\s+(?:three|3)|option\s+3|#3|3)\b", t):
        sc = dict(SCENARIOS["job_interview"])
        sc["key"] = "job_interview"
        return sc

    # Custom roleplay patterns: "act as X", "pretend you are X", "I am X and you are Y"
    m_act = re.search(r"\b(?:act\s+as|pretend\s+(?:to\s+be|you\s+are)|be)\s+(?:a|an|the|my)?\s*([a-zA-Z\s]{3,35})\b", t)
    if m_act:
        role_name = m_act.group(1).strip()
        return {
            "key": "custom",
            "title": f"Roleplay: {role_name.title()}",
            "role": role_name,
            "desc": f"Custom roleplay where you act as {role_name} in character.",
            "opener": f"Hello! Let's get right into our roleplay where I am {role_name}.",
        }

    m_custom = re.search(r"\b(?:i\s+am|i'm)\s+(?:a|an)?\s*([a-zA-Z\s]+?)\s+(?:and\s+)?(?:you\s+are|you're|act\s+as)\s+(?:a|an)?\s*([a-zA-Z\s]+)", t)
    if m_custom:
        u_role = m_custom.group(1).strip()
        ai_role = m_custom.group(2).strip()
        return {
            "key": "custom",
            "title": f"Roleplay: {ai_role.title()}",
            "role": ai_role,
            "desc": f"Custom roleplay where user is {u_role} and you act as {ai_role} in character.",
            "opener": f"Hello! Starting our roleplay — I am {ai_role} and you are {u_role}.",
        }

    return None


def is_roleplay_change_request(text: str) -> bool:
    """Detect if the user wants to switch scenarios or restart topic selection."""
    if not text:
        return False
    t = text.lower().strip()
    return bool(re.search(
        r"\b(?:change\s+(?:the\s+)?(?:scenario|topic|roleplay)|switch\s+(?:to\s+)?(?:another\s+)?(?:scenario|roleplay|topic)|"
        r"different\s+(?:scenario|roleplay|topic)|another\s+(?:scenario|roleplay|topic)|new\s+(?:scenario|roleplay|topic)|"
        r"restart\s+roleplay|exit\s+roleplay|choose\s+(?:a\s+)?(?:another|new|different)\s+(?:scenario|topic|roleplay))\b",
        t
    ))


def is_roleplay_confirmation(text: str) -> bool:
    """Detect if the user is confirming readiness to start the agreed roleplay."""
    if not text:
        return False
    t = text.lower().strip()
    return bool(re.search(
        r"\b(?:yes|yeah|sure|yep|yup|ok|okay|let's\s+(?:start|begin|do\s+it|go)|ready|i'm\s+ready|"
        r"sounds\s+good|perfect|start\s+(?:now|the\s+roleplay)|begin|let's\s+play|let's\s+try)\b",
        t
    ))
