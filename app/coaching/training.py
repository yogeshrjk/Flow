"""Personalised AI English Trainer: curriculum, learning paths, exercises,
mastery tracking, and interactive training across 10 core learning areas."""
import re
import time
from dataclasses import dataclass, field


# --- 10 Core Learning Areas with Structured Lessons -------------------------

LEARNING_AREAS = [
    {
        "id": "basic_structure",
        "title": "Basic Sentence Structure",
        "desc": "Mastering Subject-Verb-Object (SVO), simple statements, and negative sentences.",
        "lessons": [
            {
                "id": "svo_order",
                "title": "Subject + Verb + Object Order",
                "level": "A1",
                "concept": "English sentences usually follow Subject (who), Verb (action), and Object (what).",
                "hindi_tip": "Hindi mein verb aakhri mein aata hai, lekin English mein verb beech mein aata hai (I tea drink nahi, I drink tea).",
                "example": "I drink coffee every morning.",
                "exercise_prompt": "Tell me what you usually eat or drink in the morning using a simple Subject-Verb-Object sentence.",
                "keywords": ["i", "eat", "drink", "have", "breakfast", "tea", "coffee", "milk", "bread", "eggs"],
            },
            {
                "id": "simple_negatives",
                "title": "Simple Negatives with Don't and Doesn't",
                "level": "A2",
                "concept": "Use 'don't' with I/you/we/they and 'doesn't' with he/she/it, followed by the base verb.",
                "hindi_tip": "He/she ke saath 'doesn't' lagta hai aur verb ka 's/es' hat jaata hai (He doesn't likes nahi, He doesn't like).",
                "example": "She doesn't drink tea, and I don't drink coffee.",
                "exercise_prompt": "Make a sentence about something you or a friend does not like to do.",
                "keywords": ["don't", "doesn't", "do not", "does not"],
            },
        ],
    },
    {
        "id": "verb_tenses",
        "title": "Verb Forms and Tenses",
        "desc": "Present, past, and continuous forms for clear, accurate timelines.",
        "lessons": [
            {
                "id": "past_simple_actions",
                "title": "Past Simple for Finished Actions",
                "level": "A2",
                "concept": "Use the past form (went, watched, ate, visited) when talking about finished events at a specific past time.",
                "hindi_tip": "Beete hue kal ke liye past tense verb use karein (Yesterday I went, not Yesterday I go).",
                "example": "Yesterday I finished my work at six and watched a movie.",
                "exercise_prompt": "Tell me two things you did yesterday evening.",
                "keywords": ["yesterday", "went", "ate", "watched", "did", "worked", "cooked", "slept", "visited", "bought", "finished", "read"],
            },
            {
                "id": "present_continuous_now",
                "title": "Present Continuous for Actions Right Now",
                "level": "A2",
                "concept": "Use am/is/are + verb-ing for actions happening right now or current temporary habits.",
                "hindi_tip": "Jo kaam abhi chal raha hai usme 'ing' lagta hai (I am practicing, She is working).",
                "example": "I am practicing English speaking right now.",
                "exercise_prompt": "Describe what you or someone around you is doing right at this moment.",
                "keywords": ["am", "is", "are", "ing"],
            },
            {
                "id": "present_perfect_experience",
                "title": "Present Perfect for Life Experiences",
                "level": "B1",
                "concept": "Use have/has + third form (past participle) for experiences without naming a specific finished time.",
                "hindi_tip": "Life ke anubhav batane ke liye 'have/has + 3rd form' (I have visited Mumbai).",
                "example": "I have visited many cities in India, but I haven't been abroad yet.",
                "exercise_prompt": "Tell me about a city, place, or famous food you have tried in your life.",
                "keywords": ["have", "has", "visited", "seen", "tried", "eaten", "been", "traveled", "lived"],
            },
        ],
    },
    {
        "id": "modal_verbs",
        "title": "Modal Verbs (Could, Would, Should)",
        "desc": "Polite requests, suggestions, possibilities, and recommendations.",
        "lessons": [
            {
                "id": "polite_requests_could_would",
                "title": "Polite Requests with 'Could' and 'Would'",
                "level": "B1",
                "concept": "'Could you...' and 'Would you mind...' sound much warmer and more professional than 'Can you' or 'Do this'.",
                "hindi_tip": "Kisi se vinamrata se kuch kehne ke liye 'Could you please...' ya 'Would you...' sabse best hai.",
                "example": "Could you please share the report with me when you are free?",
                "exercise_prompt": "Ask me politely to send you some practice notes or help you with English.",
                "keywords": ["could", "would", "please", "mind", "help", "send", "share"],
            },
            {
                "id": "giving_advice_should",
                "title": "Giving Constructive Advice with 'Should'",
                "level": "A2",
                "concept": "Use 'should' or 'shouldn't' followed by a base verb to give friendly, supportive advice.",
                "hindi_tip": "Salah ya sujhaav dene ke liye 'should' use karein (You should drink more water).",
                "example": "You should practice speaking for ten minutes every day.",
                "exercise_prompt": "Give a piece of good advice to someone who wants to stay healthy or learn a skill.",
                "keywords": ["should", "shouldn't", "ought", "better"],
            },
        ],
    },
    {
        "id": "questions_answers",
        "title": "Questions and Answers",
        "desc": "Forming questions naturally using auxiliary verbs and Wh- words.",
        "lessons": [
            {
                "id": "wh_questions",
                "title": "Wh- Questions (What, Where, When, Why, How)",
                "level": "A2",
                "concept": "Wh- questions place the question word first, followed by an auxiliary verb (do/does/did/is/can), then subject.",
                "hindi_tip": "English mein 'Why you are late' galat hai; 'Why are you late' sahi sequence hai.",
                "example": "Where do you work, and what do you enjoy most about it?",
                "exercise_prompt": "Ask me an interesting question about my hobbies or daily routine.",
                "keywords": ["what", "where", "when", "why", "how", "who", "which", "?"],
            },
            {
                "id": "open_answers",
                "title": "Answering with Elaboration",
                "level": "B1",
                "concept": "Never answer with just 'yes' or 'no'. Answer the question, then add one extra reason or detail.",
                "hindi_tip": "Sirf 'Yes' ya 'No' mat bolo; ek line ka reason ya detail jod kar bolo.",
                "example": "Yes, I love traveling because it helps me see new cultures and unwind.",
                "exercise_prompt": "Do you enjoy cooking? Answer me with 'Yes' or 'No', plus one reason why.",
                "keywords": ["because", "since", "so", "and", "but", "enjoy", "like", "cook"],
            },
        ],
    },
    {
        "id": "articles_prepositions",
        "title": "Articles and Prepositions",
        "desc": "Mastering a, an, the, and prepositions of time, place, and direction.",
        "lessons": [
            {
                "id": "articles_a_an_the",
                "title": "Articles: A, An, and The",
                "level": "A2",
                "concept": "Use 'a' before consonant sounds, 'an' before vowel sounds, and 'the' for specific, already-known items.",
                "hindi_tip": "Sound par dhyan dein: 'an hour' (h silent), 'a university' (yoo sound).",
                "example": "I bought an apple and a sandwich. The sandwich was delicious.",
                "exercise_prompt": "Describe two things on your desk or in your room using 'a', 'an', or 'the'.",
                "keywords": ["a", "an", "the"],
            },
            {
                "id": "prepositions_in_on_at",
                "title": "Time and Place Prepositions: In, On, At",
                "level": "B1",
                "concept": "Time: 'at 7 PM' (exact time), 'on Monday' (days/dates), 'in July' / 'in the morning' (longer periods).",
                "hindi_tip": "Clock time ke liye 'at', dino ke liye 'on', mahino aur saalo ke liye 'in'.",
                "example": "Our meeting is at 10 AM on Wednesday in the conference room.",
                "exercise_prompt": "Tell me what time and day you usually have your busiest hours.",
                "keywords": ["at", "on", "in"],
            },
        ],
    },
    {
        "id": "common_errors",
        "title": "Common Grammar Errors",
        "desc": "Fixing common Indian English traps like 'didn't went', 'discuss about', and duration phrasing.",
        "lessons": [
            {
                "id": "didnt_base_verb",
                "title": "After Didn't, Always Base Verb",
                "level": "B1",
                "concept": "'Didn't' already carries the past tense. Always follow it with base form (didn't go, didn't see, didn't know).",
                "hindi_tip": "'Didn't went' bilkul galat hai; 'didn't go' bolna hai.",
                "example": "I didn't receive your email yesterday, so I didn't reply.",
                "exercise_prompt": "Tell me about something you did not buy or did not do recently using 'didn't'.",
                "keywords": ["didn't", "did not"],
            },
            {
                "id": "for_vs_since_duration",
                "title": "Duration with 'For' and 'Since'",
                "level": "B1",
                "concept": "Use 'for' with a duration (for three years, for two hours). Use 'since' with a starting point (since 2021, since morning). Never say 'from last two years'.",
                "hindi_tip": "'From last two years' galat hai; 'for two years' ya 'since 2022' bolein.",
                "example": "I have been living in this city for four years, since 2020.",
                "exercise_prompt": "How long have you been studying, working, or living in your current city?",
                "keywords": ["for", "since", "years", "months", "living", "working", "studying"],
            },
        ],
    },
    {
        "id": "vocabulary_formation",
        "title": "Vocabulary and Sentence Formation",
        "desc": "Expanding your expressive range with precise collocations and connectors.",
        "lessons": [
            {
                "id": "strong_collocations",
                "title": "Natural Word Pairings (Collocations)",
                "level": "B1",
                "concept": "English uses fixed pairs: 'make a decision' (not do a decision), 'take a break', 'pay attention', 'heavy rain'.",
                "hindi_tip": "Direct translation mat karein: 'Make a decision' hota hai, 'do a decision' nahi.",
                "example": "It took me a long time to make a decision about my career change.",
                "exercise_prompt": "Make a sentence using either 'make a decision' or 'take a break'.",
                "keywords": ["make a decision", "made a decision", "take a break", "took a break", "pay attention"],
            },
            {
                "id": "linking_words",
                "title": "Connecting Thoughts with Linking Words",
                "level": "B2",
                "concept": "Use connectors like 'however', 'although', 'moreover', and 'therefore' to connect ideas smoothly.",
                "hindi_tip": "Do vichaaron ko jodne ke liye 'however' ya 'although' ka prayog karein.",
                "example": "Although the project was challenging, our team delivered it on time.",
                "exercise_prompt": "Combine two contrasting ideas about your work or studies using 'although' or 'however'.",
                "keywords": ["although", "however", "therefore", "moreover", "on the other hand", "despite", "while"],
            },
        ],
    },
    {
        "id": "everyday_conversations",
        "title": "Everyday Conversations",
        "desc": "Small talk, social banter, shopping, and everyday interactions.",
        "lessons": [
            {
                "id": "casual_small_talk",
                "title": "Casual Small Talk and Weekend Plans",
                "level": "A2",
                "concept": "Small talk connects people comfortably: comment on the weather, weekend, or daily routine with easy questions.",
                "hindi_tip": "Casual baatchit mein mausam, weekend ya routine par halki baatein karein.",
                "example": "The weather is really pleasant today! Do you have any plans for the weekend?",
                "exercise_prompt": "Ask me what I like to do on weekends, and tell me one thing you usually do.",
                "keywords": ["weekend", "plans", "weather", "usually", "relax", "hang out", "friends"],
            },
            {
                "id": "polite_ordering_dining",
                "title": "Ordering Food and Drinks Comfortably",
                "level": "B1",
                "concept": "At a cafe or restaurant, say 'I'll have...', 'Could I get...', or 'May I please have...'. Avoid saying 'Give me'.",
                "hindi_tip": "'Give me tea' rude lagta hai; 'Could I get a cup of tea, please?' bolein.",
                "example": "Could I please get a hot cappuccino with oat milk, and a toasted bagel?",
                "exercise_prompt": "Imagine you are at a nice coffee shop. Order your favorite drink and a snack politely.",
                "keywords": ["could i get", "i'll have", "may i have", "please", "coffee", "tea", "water", "order"],
            },
        ],
    },
    {
        "id": "workplace_communication",
        "title": "Workplace Communication",
        "desc": "Standups, updates, disagreeing politely, and professional clarity.",
        "lessons": [
            {
                "id": "daily_standup_update",
                "title": "Delivering a Crisp Daily Standup Update",
                "level": "B1",
                "concept": "Structure: 1) What you completed, 2) What you are working on today, 3) Any blockers.",
                "hindi_tip": "Workplace standup mein teen baatein: Kal kya kiya, aaj kya kar rahe ho, koi dikkat toh nahi.",
                "example": "Yesterday I wrapped up the login bug. Today I am testing the payment flow, and I have no blockers.",
                "exercise_prompt": "Give me a mock 2-sentence standup update about your current work or project.",
                "keywords": ["yesterday", "today", "working on", "completed", "blocker", "testing", "finished", "tasks"],
            },
            {
                "id": "diplomatic_disagreement",
                "title": "Disagreeing Politely in Meetings",
                "level": "B2",
                "concept": "Avoid 'You are wrong'. Use softening phrases: 'I see your point, but...', 'Have we considered...', 'From my perspective...'.",
                "hindi_tip": "Sidha 'You are wrong' mat bolo; 'I see what you mean, but from my perspective...' bolein.",
                "example": "I see your point, but from my perspective, taking an extra day will ensure better quality.",
                "exercise_prompt": "Disagree politely with a colleague who wants to rush a project launch.",
                "keywords": ["i see your point", "from my perspective", "have we considered", "another angle", "however"],
            },
        ],
    },
    {
        "id": "fluency_connected_speech",
        "title": "Fluency and Connected Speech",
        "desc": "Natural rhythm, spoken contractions, linking words, and smooth phrasing.",
        "lessons": [
            {
                "id": "natural_contractions",
                "title": "Spoken Contractions (I'm, You'll, Didn't, We've)",
                "level": "B1",
                "concept": "Native and fluent speakers use contractions in spoken English to sound fluid and relaxed rather than robotic.",
                "hindi_tip": "Bolte waqt 'I am' ke bajay 'I'm', 'Do not' ke bajay 'Don't' bolne se flow natural lagta hai.",
                "example": "I'm sure you'll do great, and we've got plenty of time to prepare.",
                "exercise_prompt": "Tell me two things you plan to do this week using contractions like 'I'm', 'I'll', or 'don't'.",
                "keywords": ["i'm", "you'll", "we've", "i'll", "don't", "can't", "it's", "they're"],
            },
            {
                "id": "linking_and_pausing",
                "title": "Thought Groups and Natural Pausing",
                "level": "B2",
                "concept": "Speak in chunks of 3-5 words (thought groups) with brief breath pauses at commas and periods, not after every single word.",
                "hindi_tip": "Har shabd ke baad mat ruko; pure phrase ko ek flow mein bolo aur period par halke se ruko.",
                "example": "When I finish my work, I like to take a walk, and listen to music.",
                "exercise_prompt": "Say a longer sentence connecting two activities with a comma and 'and', speaking the first clause smoothly.",
                "keywords": ["when", "and", "because", "while", "after", "then"],
            },
        ],
    },
]

# Quick lesson lookup by id
LESSON_MAP = {}
for area in LEARNING_AREAS:
    for les in area["lessons"]:
        LESSON_MAP[les["id"]] = {**les, "area_id": area["id"], "area_title": area["title"]}


# --- Training State Machine --------------------------------------------------

@dataclass
class TrainingSessionState:
    """In-memory active state of the training session for an active connection."""
    area_id: str = "basic_structure"
    lesson_id: str = "svo_order"
    stage: str = "INTRO"  # "INTRO" (explaining topic + asking exercise) | "PRACTICE" (evaluating attempt)
    attempts: int = 0
    current_prompt: str = ""
    active: bool = False
    turn_count: int = 0


def get_default_training_profile(user_id: str = "default", level: str = "B1") -> dict:
    """Initial default training profile schema stored in per-user JSON."""
    return {
        "current_level": level or "B1",
        "current_area": "basic_structure",
        "current_lesson": "svo_order",
        "learning_goals": ["Everyday conversations", "Fluency and connected speech"],
        "mastered_topics": [],
        "practice_needed": [],
        "repeated_errors": {},
        "consecutive_passes": {},
        "exercise_history": [],
        "updated_at": time.time(),
    }


def _tr(profile: dict) -> dict:
    """Helper to extract the training dictionary whether the full user profile
    or the training sub-dict itself is passed."""
    return profile.get("training", profile) if isinstance(profile, dict) else {}


def get_learning_path(profile: dict) -> list[dict]:
    """Generates an ordered curriculum path tailored to user level and progress."""
    tr = _tr(profile)
    mastered = set(tr.get("mastered_topics", []))
    practice = set(tr.get("practice_needed", []))
    path = []
    for area in LEARNING_AREAS:
        for les in area["lessons"]:
            status = "mastered" if les["id"] in mastered else ("needs_practice" if les["id"] in practice else "up_next")
            path.append({
                "lesson_id": les["id"],
                "area_id": area["id"],
                "area_title": area["title"],
                "title": les["title"],
                "level": les["level"],
                "status": status,
            })
    return path


def get_active_lesson(profile: dict) -> dict:
    """Returns the currently active lesson that the student is training on.
    Sticks strictly to this lesson until explicit topic change."""
    tr = _tr(profile)
    current_id = tr.get("current_lesson")
    if current_id and current_id in LESSON_MAP:
        return LESSON_MAP[current_id]
    mastered = set(tr.get("mastered_topics", []))
    for area in LEARNING_AREAS:
        for les in area["lessons"]:
            if les["id"] not in mastered:
                tr["current_lesson"] = les["id"]
                tr["current_area"] = area["id"]
                return LESSON_MAP[les["id"]]
    chosen = LESSON_MAP["svo_order"]
    tr["current_lesson"] = chosen["id"]
    tr["current_area"] = chosen["area_id"]
    return chosen


def is_lesson_mastered(profile: dict, lesson_id: str) -> bool:
    """Returns True if the lesson is marked as mastered in the profile."""
    tr = _tr(profile)
    return lesson_id in tr.get("mastered_topics", [])


TOPIC_CHANGE_PATTERNS = [
    r"\b(?:next|change|switch|different|another|new)\s+(?:topic|lesson|module|concept|area|exercise)\b",
    r"\b(?:let'?s|can\s+we|i\s+want\s+to|please|let\s+us)\s+(?:move\s+on|change\s+topic|switch\s+topic|next\s+topic|try\s+another|try\s+something\s+else)\b",
    r"\b(?:move\s+on|move\s+ahead|skip\s+this|done\s+with\s+this|next\s+one)\b",
    r"\b(?:teach|practice|learn|do)\s+something\s+else\b",
]


def is_topic_change_request(text: str) -> bool:
    """Detects if the user explicitly requested to change or advance the training topic."""
    if not text:
        return False
    t = text.lower().strip()
    for pat in TOPIC_CHANGE_PATTERNS:
        if re.search(pat, t):
            return True
    if find_matching_lesson(text):
        return True
    return False


def find_matching_lesson(text: str) -> str | None:
    """Maps natural language topic mentions (e.g., 'past tense', 'modal verbs') to a lesson ID."""
    if not text:
        return None
    t = text.lower().strip()
    is_topic_intent = any(k in t for k in ("practice", "teach", "learn", "study", "do", "explain", "about", "topic", "lesson", "switch", "change", "next"))
    if not is_topic_intent:
        return None
    for lid, les in LESSON_MAP.items():
        title = les["title"].lower()
        lid_phrase = lid.replace("_", " ")
        if title in t or lid_phrase in t:
            return lid
    if "past" in t and ("simple" in t or "tense" in t or "action" in t):
        return "past_simple_actions"
    if "continuous" in t or "right now" in t:
        return "present_continuous_now"
    if "perfect" in t or "experience" in t:
        return "present_perfect_experience"
    if "modal" in t or "could and would" in t or "polite request" in t:
        return "polite_requests_could_would"
    if "advice" in t:
        return "giving_advice_should"
    if "negative" in t or "don't and doesn't" in t:
        return "simple_negatives"
    if "svo" in t or "sentence structure" in t or "basic structure" in t:
        return "svo_order"
    if "question" in t or "do and does" in t:
        return "do_does_questions"
    if "preposition" in t or "in on at" in t:
        return "prepositions_time_place"
    if "article" in t or "a an the" in t:
        return "articles_a_an_the"
    if "fluency" in t or "connected speech" in t:
        return "fluency_connected_speech"
    return None


def advance_to_next_lesson(profile: dict, requested_lesson_id: str | None = None) -> dict:
    """Explicitly advances to the next curriculum lesson or a requested topic when explicitly requested."""
    if "training" not in profile:
        profile["training"] = get_default_training_profile(profile.get("user_id", "default"), profile.get("level", "B1"))
    tr = profile["training"]

    if requested_lesson_id and requested_lesson_id in LESSON_MAP:
        chosen = LESSON_MAP[requested_lesson_id]
        tr["current_lesson"] = chosen["id"]
        tr["current_area"] = chosen["area_id"]
        tr["consecutive_passes"] = tr.get("consecutive_passes", {})
        tr["consecutive_passes"][chosen["id"]] = 0
        return chosen

    current_id = tr.get("current_lesson", "svo_order")
    mastered = set(tr.get("mastered_topics", []))

    all_lessons = []
    for area in LEARNING_AREAS:
        for les in area["lessons"]:
            all_lessons.append(les["id"])

    # Search forward from current lesson for next unmastered lesson
    if current_id in all_lessons:
        curr_idx = all_lessons.index(current_id)
        for lid in all_lessons[curr_idx + 1:]:
            if lid not in mastered:
                chosen = LESSON_MAP[lid]
                tr["current_lesson"] = chosen["id"]
                tr["current_area"] = chosen["area_id"]
                tr["consecutive_passes"] = tr.get("consecutive_passes", {})
                tr["consecutive_passes"][chosen["id"]] = 0
                return chosen

    # Search from start
    for lid in all_lessons:
        if lid not in mastered and lid != current_id:
            chosen = LESSON_MAP[lid]
            tr["current_lesson"] = chosen["id"]
            tr["current_area"] = chosen["area_id"]
            tr["consecutive_passes"] = tr.get("consecutive_passes", {})
            tr["consecutive_passes"][chosen["id"]] = 0
            return chosen

    # If all mastered, cycle forward
    if current_id in all_lessons:
        next_idx = (all_lessons.index(current_id) + 1) % len(all_lessons)
        chosen = LESSON_MAP[all_lessons[next_idx]]
    else:
        chosen = LESSON_MAP["svo_order"]

    tr["current_lesson"] = chosen["id"]
    tr["current_area"] = chosen["area_id"]
    tr["consecutive_passes"] = tr.get("consecutive_passes", {})
    tr["consecutive_passes"][chosen["id"]] = 0
    return chosen


def select_next_lesson(profile: dict) -> dict:
    """Chooses the next logical lesson: prioritizes topics needing practice,
    then the next unmastered lesson in the learning path."""
    tr = _tr(profile)
    practice = tr.get("practice_needed", [])
    mastered = set(tr.get("mastered_topics", []))

    # 1. Review topics needing practice first
    for lid in practice:
        if lid in LESSON_MAP and lid not in mastered:
            return LESSON_MAP[lid]

    # 2. Sequential progress through learning areas
    for area in LEARNING_AREAS:
        for les in area["lessons"]:
            if les["id"] not in mastered:
                return LESSON_MAP[les["id"]]

    # 3. All mastered -> start cyclical enrichment review
    return LESSON_MAP.get("fluency_connected_speech", LESSON_MAP["svo_order"])


def update_training_progress(profile: dict, lesson_id: str, passed: bool, mistakes: list | None = None) -> dict:
    """Updates the user's persistent learning path:
    - Requires 2 consecutive successful exercises to mark a topic as mastered.
    - Adds repeated mistakes to practice_needed.
    - Persists without creating fake historical data.
    - Preserves current_lesson (topic stickiness) so the learner stays on this topic until explicit request."""
    if "training" not in profile:
        profile["training"] = get_default_training_profile(profile.get("user_id", "default"), profile.get("level", "B1"))
    tr = profile["training"]

    passes = tr.setdefault("consecutive_passes", {})
    mastered = set(tr.setdefault("mastered_topics", []))
    practice = set(tr.setdefault("practice_needed", []))
    rep_errors = tr.setdefault("repeated_errors", {})

    # Record mistake patterns
    for m in (mistakes or []):
        kind = getattr(m, "kind", str(m))
        rep_errors[kind] = rep_errors.get(kind, 0) + 1
        if rep_errors[kind] >= 2 and lesson_id not in practice:
            practice.add(lesson_id)

    if passed:
        passes[lesson_id] = passes.get(lesson_id, 0) + 1
        if passes[lesson_id] >= 2:
            mastered.add(lesson_id)
            if lesson_id in practice:
                practice.discard(lesson_id)
    else:
        passes[lesson_id] = 0
        practice.add(lesson_id)

    tr["mastered_topics"] = list(mastered)
    tr["practice_needed"] = list(practice)
    tr["current_lesson"] = lesson_id

    # Append to recent exercise history (capped at 20)
    history = tr.setdefault("exercise_history", [])
    history.append({
        "lesson_id": lesson_id,
        "passed": bool(passed),
        "mistake_count": len(mistakes or []),
        "timestamp": time.time(),
    })
    tr["exercise_history"] = history[-20:]
    tr["updated_at"] = time.time()
    return profile


def build_trainer_prompt(lesson: dict, profile: dict, stage: str = "INTRO",
                         user_response: str = "", speaking_stats: dict | None = None,
                         detected_mistakes: list | None = None,
                         is_mastered: bool = False, topic_changed: bool = False) -> str:
    """Constructs a supportive, evidence-based prompt instructing the LLM to act
    as the personalized AI English Trainer."""
    stats = speaking_stats or {}
    wpm = stats.get("words_per_minute")
    pauses = stats.get("mid_sentence_pauses_count", 0)

    audio_feedback_rules = []
    if wpm is not None:
        audio_feedback_rules.append(f"- Measured speaking speed: {wpm:.1f} WPM.")
    if pauses and pauses > 0:
        audio_feedback_rules.append(f"- Note: learner had {pauses} mid-sentence hesitation pause(s). Gently encourage smooth delivery.")

    mistake_hints = []
    for m in (detected_mistakes or []):
        hint = getattr(m, "correction_hint", "")
        pat = getattr(m, "pattern", "")
        if hint:
            mistake_hints.append(f"• Found '{pat}': {hint}")

    guidance = (
        "ROLE: Personalised AI English Trainer.\n"
        f"CURRENT LESSON: {lesson['title']} (Area: {lesson.get('area_title', '')}, Level: {lesson.get('level', 'A2')}).\n"
        f"KEY CONCEPT: {lesson['concept']}\n"
        f"HINDI TIP (use only if student needs clarification): {lesson.get('hindi_tip', '')}\n"
        f"GOOD EXAMPLE: \"{lesson['example']}\"\n\n"
        "TEACHING STRUCTURE (Spoken Live Audio Chat):\n"
        "1. Keep your reply concise (1 to 2 sentences maximum, under 45 words). Talk warmly like a human coach.\n"
        "2. When evaluating the student's attempt:\n"
        "   - Respond naturally to their idea.\n"
        "   - If there is a grammatical mistake, provide the NATURAL correct sentence directly, explain briefly why, and encourage them.\n"
        "   - Distinguish grammar accuracy from speaking flow/hesitation.\n"
        "3. Plain conversational English with occasional bracketed Hindi hint if beginner. NO markdown headings, no bullet lists, no jargon.\n"
        "4. TOPIC STICKINESS: You MUST stay on the current topic until the student masters it AND explicitly asks to change topic.\n"
    )

    if topic_changed:
        guidance += (
            f"\nTASK: The user explicitly requested to switch topics. Welcome the change warmly in 1 sentence. "
            f"Introduce the new topic ({lesson['title']}) simply, give the example (\"{lesson['example']}\"), and ask: "
            f"\"{lesson['exercise_prompt']}\""
        )
    elif stage == "INTRO":
        guidance += (
            f"\nTASK: Introduce this lesson ({lesson['title']}) warmly to the learner. "
            f"Explain the concept simply, give the example (\"{lesson['example']}\"), and ask: "
            f"\"{lesson['exercise_prompt']}\""
        )
    elif stage == "HELP":
        guidance += (
            f"\nSTUDENT'S SPOKEN MESSAGE:\n\"{user_response}\"\n"
            f"TARGET EXERCISE: {lesson['exercise_prompt']}\n"
            "\nTASK: The learner expressed uncertainty or asked for help. Support them warmly in 1-2 natural sentences, "
            f"offer the example (\"{lesson['example']}\"), and invite them to try whenever they're ready. Do NOT treat this as a failed exercise."
        )
    elif is_mastered:
        guidance += (
            f"\nSTUDENT'S SPOKEN ATTEMPT:\n\"{user_response}\"\n"
            f"TARGET EXERCISE: {lesson['exercise_prompt']}\n"
            "\nTOPIC STATUS: MASTERED!\n"
            "TASK: Praise the student warmly for mastering this topic (1-2 sentences). "
            "CRITICAL: Do NOT automatically jump to another topic! Stick to this topic. "
            "Tell them: 'You've mastered this topic! When you're ready, say \"next topic\" to move on, or we can keep practicing this.' "
            "Give them one more fun sentence challenge on this same concept."
        )
    else:
        guidance += (
            f"\nSTUDENT'S SPOKEN ATTEMPT:\n\"{user_response}\"\n"
            f"TARGET EXERCISE: {lesson['exercise_prompt']}\n"
        )
        if mistake_hints:
            guidance += "DETECTED GRAMMAR ISSUES:\n" + "\n".join(mistake_hints) + "\n"
        if audio_feedback_rules:
            guidance += "SPEAKING METRICS (Acoustic evidence):\n" + "\n".join(audio_feedback_rules) + "\n"
        guidance += (
            f"\nTASK: Evaluate the student's attempt concisely (1-2 sentences). Acknowledge their idea naturally. "
            "If there were errors, model the corrected sentence warmly. "
            f"Then give ANOTHER practice sentence challenge on THIS SAME TOPIC ({lesson['title']}) to help them practice towards mastery. "
            "STAY ON THIS TOPIC — do not switch topics."
        )
    return guidance


def attempt_matches_lesson(lesson: dict, text: str) -> bool:
    """Conservative evidence gate, not an automated mastery certificate."""
    words = re.findall(r"[a-z]+(?:'[a-z]+)?", text.lower())
    if len(words) < 5:
        return False
    keywords = [k.lower() for k in lesson.get("keywords", []) if len(k) > 2 and k not in ("the", "you", "are", "have")]
    return bool(keywords) and any(re.search(r"\b" + re.escape(k) + r"\b", text.lower()) for k in keywords)
