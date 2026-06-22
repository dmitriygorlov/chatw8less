import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

# Telegram's API token and allowed user IDs
TELEGRAM_API_TOKEN = os.getenv("TELEGRAM_API_TOKEN")
raw_ids = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS = set(int(uid) for uid in raw_ids.split(",") if uid.strip().isdigit())

# Support contact (Telegram username without @)
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "test")

# OpenAI API settings
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Number of recent user/assistant dialog pairs included in assistant context.
# Nutrition calculations intentionally never use conversation history.
ASSISTANT_CONTEXT_DIALOGS = max(0, int(os.getenv("ASSISTANT_CONTEXT_DIALOGS") or 3))

# Text models configured via env
GPT_MODEL_FAST = os.getenv("GPT_MODEL_FAST", "gpt-4o-mini")
GPT_MODEL_SMART = os.getenv("GPT_MODEL_SMART", GPT_MODEL_FAST)
GPT_MODEL_ASSISTANT = os.getenv("GPT_MODEL_ASSISTANT", GPT_MODEL_SMART)

# Optional overrides for vision-capable variants
GPT_MODEL_FAST_VISION = os.getenv("GPT_MODEL_FAST_VISION") or None
GPT_MODEL_SMART_VISION = os.getenv("GPT_MODEL_SMART_VISION") or None

# Optional reasoning effort for GPT-5-class models. Leave empty for models that
# do not support reasoning parameters, such as GPT-4.1.
_REASONING_EFFORTS = {"minimal", "low", "medium", "high"}
_REASONING_ALIASES = {"none": "minimal"}


def _reasoning_effort_from_env(name: str) -> str | None:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in {"", "0", "false", "off"}:
        return None
    normalized = _REASONING_ALIASES.get(raw, raw)
    return normalized if normalized in _REASONING_EFFORTS else None


GPT_REASONING_FAST = _reasoning_effort_from_env("GPT_REASONING_FAST")
GPT_REASONING_SMART = _reasoning_effort_from_env("GPT_REASONING_SMART")
GPT_REASONING_ASSISTANT = (
    _reasoning_effort_from_env("GPT_REASONING_ASSISTANT")
    if os.getenv("GPT_REASONING_ASSISTANT") is not None
    else GPT_REASONING_SMART
)

# Per-mode token limits: reasoning models need bigger budget
MAX_TOKENS_FAST = int(os.getenv("MAX_TOKENS_FAST") or 2048)
MAX_TOKENS_SMART = int(os.getenv("MAX_TOKENS_SMART") or 4096)
MAX_TOKENS_ASSISTANT = int(os.getenv("MAX_TOKENS_ASSISTANT") or MAX_TOKENS_SMART)

MODEL_MODES = {
    "fast": {
        "label": "⚡ Быстрая",
        "model": GPT_MODEL_FAST,
        "vision_model": GPT_MODEL_FAST_VISION or GPT_MODEL_FAST,
        "max_tokens": MAX_TOKENS_FAST,
        "reasoning_effort": GPT_REASONING_FAST,
    },
    "smart": {
        "label": "🧠 Умная",
        "model": GPT_MODEL_SMART,
        "vision_model": GPT_MODEL_SMART_VISION or GPT_MODEL_SMART,
        "max_tokens": MAX_TOKENS_SMART,
        "reasoning_effort": GPT_REASONING_SMART,
    },
}

DEFAULT_MODEL_MODE = (os.getenv("DEFAULT_MODEL_MODE") or "fast").lower()
if DEFAULT_MODEL_MODE not in MODEL_MODES:
    DEFAULT_MODEL_MODE = "fast"

DEFAULT_GPT_MODEL = MODEL_MODES[DEFAULT_MODEL_MODE]["model"]
DEFAULT_VISION_MODEL = MODEL_MODES[DEFAULT_MODEL_MODE]["vision_model"]

SYSTEM_PROMPT_FUNC = (
    "You are a friendly and knowledgeable assistant specializing in nutritional guidance, weight loss, and healthy eating. "
    "Your role is to help users analyze food items, calculate calories and macronutrients, and provide motivational advice, "
    "Respond in the language of the user's query. When a structured response is required, provide a JSON object following the "
    "predefined schema. For example, the JSON must include:\n"
    "- intro_message: a brief introductory statement\n"
    "- items: an array with detailed data for each food item (name, amount_grams, calories, protein, fat, carbs)\n"
    "- fun_addition: a playful extra: joke, fun fact, mini‑fitness idea or cooking tip\n\n"
    "⚠️ All numeric fields (amount_grams, calories, protein, fat, carbs) **must** be non-null numbers. "
    "If an exact value cannot be determined, **estimate** a reasonable average rather than returning null.\n\n"
    "Do not include total or summary fields—these will be calculated on the client side. "
    "Prioritize accuracy and clarity in nutritional information, and maintain a supportive tone that encourages healthy habits. "
    "Ensure your responses are clear, concise, and friendly."
)

# OpenAI API parameters with defaults (legacy; prefer mode-specific max_tokens)
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS") or MAX_TOKENS_FAST)

# OpenAI photo
OPENAI_PHOTO_DETAIL = os.getenv("OPENAI_PHOTO_DETAIL") or "high"
SYSTEM_PROMPT_VISION = (
    "You are an expert in food recognition and nutrition analysis. "
    "Given a photo of food, return a detailed, clear list of all food items and drinks shown in the image. "
    "For each item, specify its name and an approximate weight in grams (do not skip weight even if you are not sure; estimate as best you can). "
    "If the user provides a comment, use it only as an additional context, but rely mainly on the photo. "
    "If there is no food in the image, say so directly and add a joke, for example about water. "
    "Always respond in concise, bullet-pointed style, suitable for further nutritional analysis. "
    "Respond in the language of the user's query. If there is no comment from user, use Russian. "
    "Format each item as: '- [Food name], ~[weight] g'."
)

# Prompt for calculating calories per 100 grams
SYSTEM_PROMPT_100 = (
    "You are a nutrition assistant. The user will describe ingredients or a recipe."
    " Provide a short answer with a generalized dish name (if possible) and the"
    " total calories, protein, fat and carbs per 100 grams of the described food."
    " Respond in the language of the user's query. Format your reply as one or"
    " two concise sentences, e.g.: 'Название: ... На 100 г: ... ккал, Б ... г, Ж"
    " ... г, У ... г.'"
)

# Prompt for online search queries (nutrition, fitness, brands)
SYSTEM_PROMPT_ONLINE = (
    "You are a knowledgeable nutrition and fitness assistant with access to current web information. "
    "When the user asks about specific brands, products, restaurants, or current nutritional data, "
    "use web search to find the most accurate and up-to-date information. "
    "Provide detailed answers about calorie content, macronutrients (protein, fat, carbs), "
    "fitness advice, or any nutrition-related questions. "
    "\n\n**IMPORTANT RULES:**\n"
    "1. If you find reliable information via web search, provide it clearly with nutritional data.\n"
    "2. If web search does NOT return relevant results or you cannot find specific information, "
    "you MUST explicitly state about it with: '⚠️' "
    "Do NOT make up or hallucinate nutritional data if sources are unavailable.\n"
    "3. When citing sources, format them naturally at the end\n"
    "do NOT use inline markdown links like [text](url) in the middle of sentences.\n"
    "4. Group all source links in a separate 'Источники:' section at the very end if multiple sources are used.\n"
    "\n"
    "Always respond in the language of the user's query (default: Russian). "
    "Be comprehensive but concise, and format your response clearly with bullet points or sections as needed."
)

# Logging settings
TOKENS_LOG_FILE = os.getenv("TOKENS_LOG_FILE") or "tokens.log"
LOG_DIR = "logs"
DATA_STORAGE_FILE = "storage_users.json"

# User data storage settings
STORAGE_DIR = "storage"
# how many decimal places to round statistics to
ROUND_STAT = 1

# how much hours to shift +/- the start/end of the day of UTC
HOUR_SHIFT = int(os.getenv("HOUR_SHIFT") or 0)

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = os.getenv("DATABASE_PATH") or str(BASE_DIR / "storage" / "chatw8less.sqlite3")
SITE_URL = os.getenv("SITE_URL", "").rstrip("/")
WEB_SESSION_DAYS = int(os.getenv("WEB_SESSION_DAYS") or 30)


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def should_use_secure_cookies(request_scheme: str | None = None) -> bool:
    mode = (os.getenv("WEB_COOKIE_SECURE") or "auto").strip().lower()
    if mode in {"1", "true", "yes", "on"}:
        return True
    if mode in {"0", "false", "no", "off"}:
        return False

    current_site_url = os.getenv("SITE_URL", SITE_URL).rstrip("/")
    if request_scheme == "https":
        return True
    return current_site_url.startswith("https://")


def should_run_telegram_in_web() -> bool:
    return env_flag("RUN_TELEGRAM_IN_WEB", default=True)
