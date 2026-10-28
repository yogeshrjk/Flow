"""User intent classification and speech artifact preprocessing for natural conversation.
Recognizes meta intents (readiness, help, mode switching, audio checks) and cleans
speech recognition disfluencies (stutters, trailing fillers) so the AI never gives
robotic or context-insensitive responses.
"""
import re
from enum import Enum


class UserIntent(str, Enum):
    CASUAL_CHAT_REQUEST = "casual_chat_request"
    LANGUAGE_SWITCH_REQUEST = "language_switch_request"
    AUDIO_CHECK = "audio_check"
    META_READY = "meta_ready"
    META_CONFUSED_OR_HELP = "meta_confused_or_help"
    EXERCISE_ATTEMPT = "exercise_attempt"
    GENERAL = "general"


# Repeated identical words: e.g. "hello hello", "I I want", "the the"
_REPEATED_WORDS_RX = re.compile(r"\b([a-zA-Z]+)(?:\s+\1\b)+", re.IGNORECASE)

# Leading disfluencies: "um", "uh", "ah", "err"
_LEADING_FILLERS_RX = re.compile(r"^\s*(?:um+|uh+|ah+|er+|hmm+)\b[\s,\.]*", re.IGNORECASE)

# Audio check patterns
_AUDIO_CHECK_RX = re.compile(
    r"^(?:(?:hey|hello|hi|yo)\b[\s,\.]*)*(?:can\s+(?:you|u)\s+hear\s+me|say\s+something(?:[,\s]+not\s+just\s+[^,!\?]+)?|are\s+you\s+there|"
    r"am\s+i\s+audible|can\s+you\s+speak|you\s+hear\s+me|testing\s+mic|hello\?+|hey\?+)(?:\s+(?:can\s+you|say\s+something))?[\?\.\!\s]*$",
    re.IGNORECASE,
)

# Requests to switch to casual conversation or stop drills
_CASUAL_CHAT_RX = re.compile(
    r"\b(?:casual\s+chat|just\s+chat|just\s+talk|just\s+chatting|casual\s+conversation|"
    r"talk\s+casually|can\s+we\s+just\s+(?:chat|talk)|want\s+to\s+do\s+(?:some\s+)?casual\s+chat|"
    r"let'?s\s+just\s+(?:chat|talk)|switch\s+to\s+(?:casual|free)|no\s+(?:more\s+)?(?:exercises?|drills?|practice))\b|"
    r"^(?:let'?s\s+chat|can\s+we\s+chat|just\s+casual|casual\s+only)$",
    re.IGNORECASE,
)

# Meta readiness: "can we start?", "ready", "let's begin"
_META_READY_RX = re.compile(
    r"^\s*(?:can\s+we\s+(?:start|begin)|shall\s+we\s+(?:start|begin)|let'?s\s+(?:start|begin)|"
    r"i'?m\s+ready|ready\s+to\s+start|ready|start|begin)[\?\.\!\s]*$",
    re.IGNORECASE,
)

# Meta confusion or asking for help with exercise
_META_HELP_RX = re.compile(
    r"\b(?:i\s+don'?t\s+know|i\s+have\s+no\s+idea|not\s+sure|what\s+do\s+you\s+mean|"
    r"what\s+does\s+that\s+mean|i\s+didn'?t\s+understand|i\s+don'?t\s+understand|"
    r"can\s+you\s+explain|give\s+me\s+an\s+example|what\s+are\s+we\s+doing|"
    r"what\s+should\s+i\s+say|can\s+we\s+talk\s+about\s+the\s+practice|"
    r"help\s+me|i'?m\s+confused|explain\s+again)\b|"
    r"^\s*(?:help|example|explain|how\s+to\s+say|what\s+to\s+say)[\?\.\!\s]*$",
    re.IGNORECASE,
)

# Trailing hesitation fragments: e.g. "I am just", "I was...", "and then"
_TRAILING_HESITATION_RX = re.compile(
    r"\b(?:i\s+am\s+just|i'm\s+just|i\s+was\s+just|and\s+then|you\s+know|i\s+mean)\s*$",
    re.IGNORECASE,
)

# Acoustic homophone slips: Whisper or browser STT can confuse unvoiced stop /t/
# with /d/ (e.g. "I add breakfast" -> "I ate breakfast", "we add lunch" -> "we ate lunch")
_ACOUSTIC_ADD_ATE_RX = re.compile(
    r"\b(i|we|they|he|she|you)\s+add\s+(a|an|the|my|our|some|breakfast|lunch|dinner|food|pizza|burger|apple|apples|meal|meals|it|them|bread|rice|snack|snacks)\b",
    re.IGNORECASE,
)
_ACOUSTIC_ALREADY_ADD_RX = re.compile(r"\b(i|we|they|he|she)\s+already\s+add\b", re.IGNORECASE)
_ACOUSTIC_HAVE_ADD_RX = re.compile(r"\b(have|has|had)\s+you\s+add\b", re.IGNORECASE)

# Mapping of all supported languages and their synonyms/aliases/native names
LANGUAGE_SYNONYMS: dict[str, list[str]] = {
    "english": ["english", "eng", "angrezi", "angreji"],
    "hindi": ["hindi", "hindustani"],
    "german": ["german", "deutsch", "germany"],
    "spanish": ["spanish", "español", "espanol", "castellano"],
    "french": ["french", "français", "francais"],
    "portuguese": ["portuguese", "português", "portugues"],
    "italian": ["italian", "italiano"],
    "dutch": ["dutch", "nederlands", "holland"],
    "russian": ["russian", "русский", "russkiy"],
    "japanese": ["japanese", "nihongo", "日本語"],
    "korean": ["korean", "hanguk", "한국어"],
    "chinese": ["chinese", "mandarin", "putonghua", "中文"],
    "arabic": ["arabic", "arabi", "العربية"],
    "tamil": ["tamil", "tamizh", "தமிழ்"],
    "telugu": ["telugu", "తెలుగు"],
    "bengali": ["bengali", "bangla", "বাংলা"],
    "marathi": ["marathi", "मराठी"],
    "urdu": ["urdu", "اردو"],
}

_LANG_SWITCH_RX = re.compile(
    r"\b(?:talk|speak|chat|converse|switch|reply|respond)\s+(?:(?:to\s+)?(?:me\s+)?(?:in\s+)?)?([a-zA-Z\u00C0-\u024F]+)\b",
    re.IGNORECASE,
)
_LANG_ONLY_RX = re.compile(
    r"^(?:please\s+)?(?:in\s+)?([a-zA-Z\u00C0-\u024F]+)(?:\s+(?:please|only|language))?$",
    re.IGNORECASE,
)
_LANG_HINDI_CMD_RX = re.compile(
    r"\b(?:hindi\s+(?:me|mein)\s+(?:bolo|baat\s+karo|baat\s+karein|baat)|kya\s+hum\s+hindi\s+me|hindi\s+bolo)\b",
    re.IGNORECASE,
)
_LANG_GERMAN_CMD_RX = re.compile(
    r"\b(?:sprich\s+(?:bitte\s+)?deutsch|deutsch\s+sprechen|können\s+wir\s+deutsch\s+sprechen|auf\s+deutsch(?:\s+bitte)?)\b",
    re.IGNORECASE,
)
_LANG_SPANISH_CMD_RX = re.compile(
    r"\b(?:habla\s+(?:en\s+)?español|hablemos\s+en\s+español|puedes\s+hablar\s+español|en\s+español(?:\s+por\s+favor)?)\b",
    re.IGNORECASE,
)
_LANG_FRENCH_CMD_RX = re.compile(
    r"\b(?:parle\s+(?:en\s+)?français|parlons\s+en\s+français|peux-tu\s+parler\s+français|en\s+français(?:\s+s'il\s+te\s+plaît)?)\b",
    re.IGNORECASE,
)


# Context-aware homophone pairs and semantic triggers
CONTEXTUAL_HOMOPHONES = [
    # (target_word, wrong_transcription, semantic_triggers)
    ("cereal", "serial", {"cereal", "eat", "drink", "morning", "breakfast", "food", "milk", "bowl", "snack"}),
    ("ate", "eight", {"eat", "ate", "food", "breakfast", "dinner", "lunch", "yesterday", "meal", "apple"}),
    ("weather", "whether", {"weather", "climate", "rain", "sunny", "forecast", "hot", "cold", "outside"}),
    ("flour", "flower", {"bake", "baking", "cake", "bread", "dough", "kitchen", "cook", "flour"}),
    ("flower", "flour", {"garden", "rose", "plant", "bloom", "floral", "flower"}),
    ("meat", "meet", {"food", "eat", "chicken", "beef", "pork", "vegetarian", "vegan", "meat", "butcher"}),
    ("piece", "peace", {"cake", "pie", "bread", "part", "slice", "bit", "puzzle", "piece"}),
    ("peace", "piece", {"war", "quiet", "calm", "world", "harmony", "peace"}),
    ("brake", "break", {"car", "drive", "stop", "pedal", "bicycle", "vehicle", "brake"}),
    ("break", "brake", {"rest", "lunch", "coffee", "pause", "holiday", "fracture", "snap", "break"}),
    ("plain", "plane", {"simple", "flavor", "yogurt", "dress", "text", "clear", "plain"}),
    ("plane", "plain", {"fly", "airport", "flight", "pilot", "aircraft", "travel", "plane"}),
    ("wait", "weight", {"time", "minute", "hold", "stay", "patient", "wait"}),
    ("weight", "wait", {"heavy", "scale", "gym", "lift", "kilos", "pounds", "fat", "loss", "weight"}),
    ("sweet", "suite", {"sugar", "dessert", "candy", "taste", "delicious", "chocolate", "sweet"}),
    ("suite", "sweet", {"hotel", "room", "office", "software", "bedroom", "suite"}),
    ("weak", "week", {"strong", "tired", "feeble", "muscle", "sick", "frail", "weak"}),
    ("week", "weak", {"day", "month", "year", "monday", "weekend", "schedule", "week"}),
    ("tail", "tale", {"dog", "cat", "animal", "wag", "fur", "tail"}),
    ("tale", "tale", {"story", "book", "tell", "fairytale", "legend", "tale"}),
    ("hear", "here", {"listen", "sound", "ear", "voice", "audio", "loud", "hear"}),
    ("here", "hear", {"place", "come", "stay", "location", "sit", "arrive", "here"}),
    ("knight", "night", {"castle", "armor", "sword", "medieval", "king", "queen", "knight"}),
    ("night", "knight", {"day", "dark", "sleep", "evening", "bed", "moon", "night"}),
    ("stair", "stare", {"step", "climb", "up", "down", "building", "floor", "stair"}),
    ("stare", "stair", {"look", "eye", "gaze", "watch", "see", "stare"}),
    ("steal", "steel", {"thief", "rob", "money", "crime", "take", "steal"}),
    ("steel", "steel", {"metal", "iron", "construction", "beam", "hard", "steel"}),
    ("root", "route", {"tree", "plant", "ground", "cause", "tooth", "hair", "root"}),
    ("route", "root", {"road", "way", "bus", "map", "drive", "gps", "travel", "route"}),
    ("son", "sun", {"daughter", "father", "mother", "child", "boy", "family", "son"}),
    ("sun", "son", {"sky", "shine", "light", "day", "solar", "warm", "sun"}),
    ("sea", "see", {"ocean", "water", "beach", "ship", "fish", "wave", "sea"}),
    ("see", "sea", {"look", "eye", "watch", "view", "vision", "notice", "see"}),
    ("allowed", "aloud", {"permission", "rules", "permit", "can", "allowed"}),
    ("aloud", "allowed", {"speak", "read", "say", "voice", "volume", "aloud"}),
    ("past", "passed", {"tense", "yesterday", "history", "grammar", "ago", "past"}),
    ("passed", "past", {"test", "exam", "car", "by", "time", "away", "passed"}),
]


def align_contextual_homophones(text: str, context: str = "") -> str:
    """Correct phonetic homophone transcription slips using the recent conversation context.
    For instance, if context mentions morning/eating/cereal and user says 'What is Serial?',
    it corrects it to 'What is cereal?'."""
    if not text or not context:
        return text

    ctx_lower = context.lower()
    ctx_words = set(re.findall(r"\b[a-z]{3,}\b", ctx_lower))

    out_text = text
    for target, wrong, triggers in CONTEXTUAL_HOMOPHONES:
        pattern = re.compile(rf"\b{re.escape(wrong)}\b", re.IGNORECASE)
        if pattern.search(out_text):
            if any(t in ctx_words or t in ctx_lower for t in triggers):
                def _rep(m):
                    orig = m.group(0)
                    if orig.isupper():
                        return target.upper()
                    if orig.istitle() and m.start() == 0:
                        return target.capitalize()
                    return target.lower()
                out_text = pattern.sub(_rep, out_text)
    return out_text


def clean_speech_artifacts(text: str, context: str = "") -> str:
    """Preprocess transcription text: deduplicate repeated words, clean disfluencies,
    and resolve common acoustic homophone errors while preserving natural meaning."""
    if not text:
        return ""
    t = text.strip()
    # Deduplicate consecutive identical words: "hello hello can you hear me" -> "hello can you hear me"
    t = _REPEATED_WORDS_RX.sub(r"\1", t)
    # Strip leading fillers if followed by real words
    cleaned = _LEADING_FILLERS_RX.sub("", t).strip()
    t = cleaned if cleaned else t
    # Fix common acoustic homophone slips where final unvoiced /t/ was heard as /d/
    t = _ACOUSTIC_ADD_ATE_RX.sub(r"\1 ate \2", t)
    t = _ACOUSTIC_ALREADY_ADD_RX.sub(r"\1 already ate", t)
    t = _ACOUSTIC_HAVE_ADD_RX.sub(r"\1 you had", t)
    # Context-aware homophone alignment (e.g. 'Serial' -> 'cereal' when context is about breakfast)
    if context:
        t = align_contextual_homophones(t, context)
    return t


def resolve_language_id(name: str | None) -> str | None:
    """Resolve a language name/synonym to its canonical language id."""
    if not name:
        return None
    key = name.strip().lower()
    for lang_id, syns in LANGUAGE_SYNONYMS.items():
        if key == lang_id or key in syns:
            return lang_id
    return None


def detect_language_switch_request(text: str) -> str | None:
    """Detect if the user is asking to switch languages (e.g. 'talk in hindi', 'sprich deutsch')."""
    if not text:
        return None
    t = text.strip()
    if _LANG_HINDI_CMD_RX.search(t):
        return "hindi"
    if _LANG_GERMAN_CMD_RX.search(t):
        return "german"
    if _LANG_SPANISH_CMD_RX.search(t):
        return "spanish"
    if _LANG_FRENCH_CMD_RX.search(t):
        return "french"

    m = _LANG_SWITCH_RX.search(t)
    if m:
        lang = resolve_language_id(m.group(1))
        if lang:
            return lang

    m_only = _LANG_ONLY_RX.match(t)
    if m_only:
        lang = resolve_language_id(m_only.group(1))
        if lang:
            return lang

    return None


def detect_language_from_content(text: str) -> str | None:
    """Detect the language of text from native scripts or strong vocabulary signatures."""
    if not text:
        return None
    t = text.strip()

    # Devanagari script: Hindi vs Marathi
    if re.search(r"[\u0900-\u097F]", t):
        if re.search(r"\b(आहे|नाही|कसे|तुम्ही|काय|करा)\b", t):
            return "marathi"
        return "hindi"

    # Arabic script: Arabic vs Urdu
    if re.search(r"[\u0600-\u06FF]", t):
        if re.search(r"\b(ہیں|ہوں|کیا|کیسے|آپ|شکریہ)\b", t):
            return "urdu"
        return "arabic"

    # Cyrillic script
    if re.search(r"[\u0400-\u04FF]", t):
        return "russian"

    # Japanese kana / kanji
    if re.search(r"[\u3040-\u309F\u30A0-\u30FF]", t):
        return "japanese"

    # Korean Hangul
    if re.search(r"[\uAC00-\uD7AF]", t):
        return "korean"

    # Chinese Hanzi
    if re.search(r"[\u4E00-\u9FFF]", t):
        return "chinese"

    # Tamil
    if re.search(r"[\u0B80-\u0BFF]", t):
        return "tamil"

    # Telugu
    if re.search(r"[\u0C00-\u0C7F]", t):
        return "telugu"

    # Bengali
    if re.search(r"[\u0980-\u09FF]", t):
        return "bengali"

    # Latin-script language signatures
    low = t.lower()
    if re.search(r"\b(ich bin|wie geht'?s|wie geht es dir|guten (?:tag|morgen|abend)|danke (?:sehr|schön)|alles gut|nicht wahr|auf wiedersehen)\b", low):
        return "german"
    if re.search(r"\b(bonjour|comment (?:allez-vous|vas-tu|ça va)|merci beaucoup|s'il vous plaît|au revoir|bonne journée|je suis|très bien)\b", low):
        return "french"
    if re.search(r"\b(hola|cómo estás|cómo está|buenos días|buenas tardes|muchas gracias|por favor|hasta luego|muy bien|de nada)\b", low):
        return "spanish"
    if re.search(r"\b(ciao|buongiorno|come stai|come va|grazie mille|per favore|molto bene|arrivederci)\b", low):
        return "italian"
    if re.search(r"\b(olá|como (?:você|vai)|tudo bem|bom dia|boa tarde|muito obrigado|por favor)\b", low):
        return "portuguese"
    if re.search(r"\b(hallo|hoe gaat het|goedendag|dank je|alstublieft|tot ziens)\b", low):
        return "dutch"
    if re.search(r"\b(kya haal hai|kaise ho|kya chal raha|theek hoon|aap kaise|hum baat|kuch nahi|mujhe lagta|accha laga)\b", low):
        return "hindi"

    return None


def has_trailing_hesitation(text: str) -> bool:
    """Detect if the user trailed off mid-thought with filler or incomplete phrasing."""
    return bool(_TRAILING_HESITATION_RX.search(text.strip()))


def is_audio_check(text: str) -> bool:
    """Check if the user is testing audio connectivity or microphone."""
    t = clean_speech_artifacts(text).strip()
    return bool(_AUDIO_CHECK_RX.match(t))


def is_casual_chat_request(text: str) -> bool:
    """Check if the user explicitly wants casual chat without drills or exercises."""
    return bool(_CASUAL_CHAT_RX.search(text.strip()))


def is_meta_ready(text: str) -> bool:
    """Check if the user is asking to start or confirming readiness."""
    return bool(_META_READY_RX.match(text.strip()))


def is_meta_help(text: str) -> bool:
    """Check if the user is expressing confusion, asking for help, or asking about the practice."""
    return bool(_META_HELP_RX.search(text.strip()))


def classify_user_intent(text: str, current_mode: str = "free") -> UserIntent:
    """Classify user turn intent so the conversational engine responds appropriately
    without misinterpreting control commands as exercise answers."""
    cleaned = clean_speech_artifacts(text).strip()
    if not cleaned:
        return UserIntent.GENERAL

    if is_audio_check(cleaned):
        return UserIntent.AUDIO_CHECK

    if is_casual_chat_request(cleaned):
        return UserIntent.CASUAL_CHAT_REQUEST

    if detect_language_switch_request(cleaned):
        return UserIntent.LANGUAGE_SWITCH_REQUEST

    if current_mode in ("training", "practice", "roleplay", "interview"):
        if is_meta_ready(cleaned):
            return UserIntent.META_READY
        if is_meta_help(cleaned):
            return UserIntent.META_CONFUSED_OR_HELP
        return UserIntent.EXERCISE_ATTEMPT

    return UserIntent.GENERAL

