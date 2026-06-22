from bot.app_services import analyze_food_text, analyze_online, save_meal
from bot.config import ASSISTANT_CONTEXT_DIALOGS
from bot.db import add_message, get_messages, get_recent_context


def _build_contextual_input(
    user_id: str,
    user_text: str,
    dialog_limit: int,
    source: str | None = None,
    exclude_source: str | None = None,
    include_assistant_messages: bool = True,
) -> str:
    history = get_recent_context(
        user_id,
        limit=dialog_limit * 2,
        source=source,
        exclude_source=exclude_source,
    )
    if not include_assistant_messages:
        history = [message for message in history if message["role"] == "user"]

    if not history:
        return user_text

    lines = [
        f"Current user message (answer this): {user_text}",
        "",
        "Conversation context for continuity. The current message may be a short follow-up.",
        "Resolve pronouns, omitted subjects, products, stores, brands, places, and comparisons from this context unless the user clearly switches topic.",
        "Use the history only to resolve references. Do not repeat old items when the current message specifies new ones.",
    ]
    for message in history:
        speaker = "User" if message["role"] == "user" else "Assistant"
        lines.append(f"{speaker}: {message['content']}")
    return "\n".join(lines)


async def process_text_interaction(
    user_id: str,
    user_text: str,
    source: str,
    auto_save_meal: bool = False,
) -> dict:
    # Every nutrition entry is a separate meal draft. Including earlier food
    # messages makes smaller models copy old products into the current result.
    result = await analyze_food_text(user_id, user_text)

    add_message(user_id, role="user", content=user_text, source=source)
    add_message(
        user_id,
        role="assistant",
        content=result["display_text"],
        source=source,
    )

    saved = None
    if auto_save_meal and result.get("items"):
        saved = save_meal(user_id, result["items"])

    return {
        "reply_text": result["display_text"],
        "items": result.get("items", []),
        "saved": saved,
        "history": get_messages(user_id),
    }


async def process_online_interaction(user_id: str, user_text: str, source: str) -> dict:
    contextual_input = _build_contextual_input(
        user_id,
        user_text,
        dialog_limit=ASSISTANT_CONTEXT_DIALOGS,
        source=source,
    )
    result = await analyze_online(user_id, contextual_input)

    add_message(user_id, role="user", content=user_text, source=source)
    add_message(user_id, role="assistant", content=result["text"], source=source)

    return {
        "reply_text": result["text"],
        "history": get_messages(user_id),
    }


def log_exchange(user_id: str, user_text: str, assistant_text: str, source: str) -> None:
    add_message(user_id, role="user", content=user_text, source=source)
    add_message(user_id, role="assistant", content=assistant_text, source=source)


def get_chat_history(user_id: str) -> list[dict]:
    return get_messages(user_id)
