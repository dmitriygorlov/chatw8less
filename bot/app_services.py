import json
import logging
import re
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile

from bot.config import (
    GPT_MODEL_ASSISTANT,
    GPT_REASONING_ASSISTANT,
    HOUR_SHIFT,
    MAX_TOKENS_ASSISTANT,
    TOKENS_LOG_FILE,
)
from bot.db import (
    get_user_assistant_name,
    get_user_language,
    set_user_assistant_name as persist_user_assistant_name,
    set_user_language as persist_user_language,
)
from bot.i18n import t
from bot.i18n import (
    DEFAULT_LANGUAGE,
    language_name,
    language_options,
    load_locale,
    normalize_language_code,
    required_locale_keys,
    save_generated_locale,
)
from bot.openai_client import (
    generate_locale_response,
    get_openai_online_response,
    get_openai_response,
    get_openai_vision_response,
)
from bot.utils import (
    extract_function_call_payload,
    format_all_statistics,
    format_day_statistics,
    format_last_7_days_statistics,
    format_log_food_data,
    get_response_finish_reason,
    get_user_model_config,
    load_user_data_new,
    log_token_usage,
    sanitize_items,
    save_user_data_new,
    set_user_model_mode,
    import_legacy_data_map,
)


def shifted_now() -> datetime:
    return datetime.now() + timedelta(hours=HOUR_SHIFT)


def current_date_str() -> str:
    return shifted_now().strftime("%Y-%m-%d")


def _parse_nutrition_payload(response: Any, user_id: str, language_code: str) -> dict[str, Any]:
    payload, _, _ = extract_function_call_payload(response)

    if isinstance(payload, str):
        parsed = json.loads(payload)
    elif isinstance(payload, dict):
        parsed = payload
    else:
        raise ValueError(f"Unsupported payload type: {type(payload)}")

    if not isinstance(parsed, dict):
        raise ValueError("Parsed nutrition payload is not an object")

    items = sanitize_items(parsed.get("items", []))
    parsed["items"] = items
    parsed["formatted_items"] = format_log_food_data({"items": items}, language_code)
    parsed["display_text"] = "\n\n".join(
        part
        for part in [
            parsed.get("intro_message"),
            parsed["formatted_items"],
            parsed.get("fun_addition"),
        ]
        if part
    )
    parsed["user_id"] = user_id
    return parsed


def _has_suspicious_zero_nutrition(parsed: dict[str, Any]) -> bool:
    items = parsed.get("items") or []
    positive_amount_items = [
        item for item in items if item.get("amount_grams", 0) > 0
    ]
    if not positive_amount_items:
        return False

    nutrition_fields = ("calories", "protein", "fat", "carbs")
    return all(
        all(item.get(field, 0) <= 0 for field in nutrition_fields)
        for item in positive_amount_items
    )


async def analyze_food_text(user_id: str, text: str) -> dict[str, Any]:
    mode, model_config = get_user_model_config(user_id)
    language_code = get_user_language(user_id)
    system_prompt = t(
        language_code,
        "prompt.food",
        language_name=language_name(language_code),
    )
    response = await get_openai_response(
        text,
        function_name="log_nutrition_data",
        system_prompt=system_prompt,
        model=model_config["model"],
        max_tokens=model_config["max_tokens"],
        reasoning_effort=model_config.get("reasoning_effort"),
    )
    retry_reason = None
    try:
        parsed = _parse_nutrition_payload(response, user_id, language_code)
        if _has_suspicious_zero_nutrition(parsed):
            retry_reason = "all nutrition values are zero for items with positive weight"
    except (TypeError, ValueError) as exc:
        retry_reason = str(exc)

    if retry_reason:
        logging.warning(
            "Retrying nutrition analysis for user %s: %s",
            user_id,
            retry_reason,
        )
        retry_max_tokens = max(model_config["max_tokens"] * 2, 4096)
        retry_text = (
            f"{text}\n\n"
            "Correction required: recalculate the CURRENT user message carefully. "
            "Do not copy food items or nutrition values from conversation history. "
            "For every food or caloric drink with a positive amount, estimate realistic "
            "calories and macros instead of returning all zeros. Zero values are valid "
            "only for genuinely calorie-free items such as water or unsweetened black coffee."
        )
        response = await get_openai_response(
            retry_text,
            function_name="log_nutrition_data",
            system_prompt=system_prompt,
            model=model_config["model"],
            max_tokens=retry_max_tokens,
            reasoning_effort=model_config.get("reasoning_effort"),
        )
        parsed = _parse_nutrition_payload(response, user_id, language_code)

    log_token_usage(
        response.usage,
        user_id,
        get_response_finish_reason(response),
        tokens_log_file=TOKENS_LOG_FILE,
        function_name=f"web_log_nutrition_data[{mode}]",
        model=model_config["model"],
        input_text=text,
        output_text=parsed.get("display_text"),
    )

    parsed["mode"] = mode
    parsed["model_label"] = t(language_code, f"mode.{mode}")
    return parsed


async def analyze_photo(user_id: str, image_url: str, caption: str = "") -> dict[str, Any]:
    mode, model_config = get_user_model_config(user_id)
    language_code = get_user_language(user_id)
    response = await get_openai_vision_response(
        image_url=image_url,
        user_comment=caption,
        prompt=t(
            language_code,
            "prompt.vision",
            language_name=language_name(language_code),
        ),
        model=model_config["vision_model"],
        max_tokens=model_config["max_tokens"],
        reasoning_effort=model_config.get("reasoning_effort"),
    )
    answer = response.output_text

    log_token_usage(
        response.usage,
        user_id,
        get_response_finish_reason(response),
        tokens_log_file=TOKENS_LOG_FILE,
        function_name=f"web_vision_food_recognition[{mode}]",
        model=model_config["vision_model"],
        input_text=caption or "[photo without caption]",
        output_text=answer,
    )

    return {
        "mode": mode,
        "model_label": t(language_code, f"mode.{mode}"),
        "recognized_text": answer,
    }


async def analyze_hundred(user_id: str, text: str) -> dict[str, str]:
    mode, model_config = get_user_model_config(user_id)
    language_code = get_user_language(user_id)
    response = await get_openai_response(
        text,
        system_prompt=t(
            language_code,
            "prompt.hundred",
            language_name=language_name(language_code),
        ),
        model=model_config["model"],
        max_tokens=model_config["max_tokens"],
        reasoning_effort=model_config.get("reasoning_effort"),
    )
    answer = response.output_text

    log_token_usage(
        response.usage,
        user_id,
        get_response_finish_reason(response),
        tokens_log_file=TOKENS_LOG_FILE,
        function_name=f"web_per_100g[{mode}]",
        model=model_config["model"],
        input_text=text,
        output_text=answer,
    )
    return {"mode": mode, "model_label": t(language_code, f"mode.{mode}"), "text": answer}


async def analyze_online(user_id: str, text: str) -> dict[str, str]:
    language_code = get_user_language(user_id)
    assistant_name = get_user_assistant_name(user_id)
    response = await get_openai_online_response(
        text,
        prompt=(
            f"Your assistant name for this user is {assistant_name}. "
            "If the user asks who you are or what your name is, answer with this name. "
            + t(
                language_code,
                "prompt.online",
                language_name=language_name(language_code),
            )
        ),
        model=GPT_MODEL_ASSISTANT,
        max_tokens=MAX_TOKENS_ASSISTANT,
        reasoning_effort=GPT_REASONING_ASSISTANT,
    )
    answer = response.output_text

    log_token_usage(
        response.usage,
        user_id,
        get_response_finish_reason(response),
        tokens_log_file=TOKENS_LOG_FILE,
        function_name="web_online_search[assistant]",
        model=GPT_MODEL_ASSISTANT,
        input_text=text,
        output_text=answer,
    )
    return {
        "mode": "assistant",
        "model_label": GPT_MODEL_ASSISTANT,
        "text": answer,
    }


def save_meal(user_id: str, items: list[dict[str, Any]], date_str: str | None = None) -> dict[str, Any]:
    normalized_items = sanitize_items(items)
    target_date = date_str or current_date_str()
    user_data = load_user_data_new(user_id)
    date_meals = user_data.setdefault(target_date, {})

    existing_numbers = [int(key) for key in date_meals.keys() if str(key).isdigit()]
    next_meal_number = max(existing_numbers, default=0) + 1
    date_meals[str(next_meal_number)] = normalized_items
    save_user_data_new(user_id, user_data)
    logging.info(
        "Saved meal for user %s: date=%s, meal=%s, items=%s",
        user_id,
        target_date,
        next_meal_number,
        [item.get("name") for item in normalized_items],
    )

    return {"date": target_date, "meal_number": next_meal_number, "items": normalized_items}


def get_user_settings(user_id: str) -> dict[str, Any]:
    mode, model_config = get_user_model_config(user_id)
    user_data = load_user_data_new(user_id)
    language_code = get_user_language(user_id)
    return {
        "user_id": user_id,
        "daily_limit": user_data.get("daily_limit"),
        "mode": mode,
        "model_label": t(language_code, f"mode.{mode}"),
        "language_code": language_code,
        "assistant_name": get_user_assistant_name(user_id),
    }


def set_daily_limit(user_id: str, limit: int | None) -> dict[str, Any]:
    user_data = load_user_data_new(user_id)
    if limit is None:
        user_data.pop("daily_limit", None)
    else:
        user_data["daily_limit"] = limit
    save_user_data_new(user_id, user_data)
    return get_user_settings(user_id)


def set_model_mode(user_id: str, mode: str) -> dict[str, Any]:
    normalized = set_user_model_mode(user_id, mode)
    settings = get_user_settings(user_id)
    settings["mode"] = normalized
    return settings


def set_language(user_id: str, language_code: str) -> dict[str, Any]:
    persist_user_language(user_id, language_code)
    return get_user_settings(user_id)


def set_assistant_name(user_id: str, assistant_name: str) -> dict[str, Any]:
    persist_user_assistant_name(user_id, assistant_name)
    return get_user_settings(user_id)


def _placeholders(value: str) -> set[str]:
    return set(re.findall(r"\{[a-zA-Z0-9_]+\}", value or ""))


def _validate_generated_messages(messages: dict[str, Any]) -> dict[str, str]:
    if not isinstance(messages, dict):
        raise ValueError("Generated locale messages must be an object")
    source = load_locale(DEFAULT_LANGUAGE)
    required_keys = required_locale_keys()
    missing = [key for key in required_keys if key not in messages]
    if missing:
        raise ValueError(f"Generated locale is missing keys: {', '.join(missing[:5])}")

    validated: dict[str, str] = {}
    for key in required_keys:
        value = messages.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Generated locale has invalid value for {key}")
        if _placeholders(source.get(key, "")) != _placeholders(value):
            raise ValueError(f"Generated locale changed placeholders for {key}")
        validated[key] = value
    return validated


async def generate_and_set_language(user_id: str, language_request: str) -> dict[str, Any]:
    requested = (language_request or "").strip()
    if not requested:
        raise ValueError("Language is required")

    existing = normalize_language_code(requested, fallback="")
    if existing:
        return set_language(user_id, existing)

    response = await generate_locale_response(requested, load_locale(DEFAULT_LANGUAGE))
    payload, _, _ = extract_function_call_payload(response)
    if isinstance(payload, str):
        parsed = json.loads(payload)
    elif isinstance(payload, dict):
        parsed = payload
    else:
        raise ValueError("Generated locale payload is invalid")

    language_code = parsed.get("language_code")
    native_name = parsed.get("native_name")
    messages = _validate_generated_messages(parsed.get("messages"))
    normalized = save_generated_locale(language_code, native_name, messages)
    persist_user_language(user_id, normalized)
    return get_user_settings(user_id)


def get_statistics_bundle(user_id: str) -> dict[str, str]:
    user_data = load_user_data_new(user_id)
    today = current_date_str()
    yesterday = (shifted_now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
    return {
        "today": format_day_statistics(user_data, today, "📅 Сегодня"),
        "yesterday": format_day_statistics(user_data, yesterday, "📆 Вчера"),
        "last7": format_last_7_days_statistics(user_data),
        "all": format_all_statistics(user_data),
    }


def get_history(user_id: str) -> list[dict[str, Any]]:
    user_data = load_user_data_new(user_id)
    history: list[dict[str, Any]] = []

    for date_key in sorted(
        [key for key in user_data.keys() if key not in {"daily_limit", "model_mode", "language_code"}],
        reverse=True,
    ):
        day_data = user_data.get(date_key, {})
        day_total = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0}
        meals: list[dict[str, Any]] = []

        for meal_no in sorted(day_data.keys(), key=int):
            items = sanitize_items(day_data[meal_no])
            meal_total = {
                "calories": round(sum(item["calories"] for item in items), 1),
                "protein": round(sum(item["protein"] for item in items), 1),
                "fat": round(sum(item["fat"] for item in items), 1),
                "carbs": round(sum(item["carbs"] for item in items), 1),
            }
            for key, value in meal_total.items():
                day_total[key] += value
            meals.append(
                {
                    "meal_number": int(meal_no),
                    "items": items,
                    "total": meal_total,
                }
            )

        history.append(
            {
                "date": date_key,
                "meals": meals,
                "total": {key: round(value, 1) for key, value in day_total.items()},
            }
        )

    return history


def delete_meal(user_id: str, date_key: str, meal_number: int) -> None:
    user_data = load_user_data_new(user_id)
    day_data = user_data.get(date_key, {})
    day_data.pop(str(meal_number), None)
    if day_data:
        user_data[date_key] = day_data
    else:
        user_data[date_key] = {}
    save_user_data_new(user_id, user_data)


def delete_item(user_id: str, date_key: str, meal_number: int, item_index: int) -> None:
    user_data = load_user_data_new(user_id)
    day_data = user_data.get(date_key, {})
    meal_items = day_data.get(str(meal_number), [])
    if item_index < 0 or item_index >= len(meal_items):
        raise IndexError("Item index out of range")

    meal_items.pop(item_index)
    if meal_items:
        day_data[str(meal_number)] = meal_items
        user_data[date_key] = day_data
    else:
        day_data.pop(str(meal_number), None)
        user_data[date_key] = day_data if day_data else {}
    save_user_data_new(user_id, user_data)


def validate_limit(value: Any) -> int | None:
    if value in ("", None):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    limit = int(value)
    if limit <= 0:
        raise ValueError("Daily limit must be positive")
    return limit


def get_statistics_bundle(user_id: str) -> dict[str, str]:
    user_data = load_user_data_new(user_id)
    language_code = get_user_language(user_id)
    today = current_date_str()
    yesterday = (shifted_now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
    return {
        "today": format_day_statistics(
            user_data,
            today,
            f"📅 {t(language_code, 'stats.today')}",
            language_code,
        ),
        "yesterday": format_day_statistics(
            user_data,
            yesterday,
            f"📆 {t(language_code, 'stats.yesterday')}",
            language_code,
        ),
        "last7": format_last_7_days_statistics(user_data, language_code),
        "all": format_all_statistics(user_data, language_code),
    }


def import_legacy_storage_zip(raw_bytes: bytes) -> dict[str, Any]:
    if not raw_bytes:
        raise ValueError("ZIP archive is empty")

    try:
        zip_file = ZipFile(BytesIO(raw_bytes))
    except BadZipFile as exc:
        raise ValueError("Invalid ZIP archive") from exc

    legacy_data_by_user: dict[str, dict[str, Any]] = {}

    with zip_file:
        for member in zip_file.infolist():
            if member.is_dir():
                continue

            member_path = PurePosixPath(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError("ZIP contains unsafe paths")

            parts = list(member_path.parts)
            if "storage" in parts:
                parts = parts[parts.index("storage") + 1 :]

            if len(parts) == 2 and parts[1] == "meta.json":
                user_id = parts[0]
                with zip_file.open(member) as src:
                    meta = json.loads(src.read().decode("utf-8"))
                user_data = legacy_data_by_user.setdefault(user_id, {})
                if isinstance(meta, dict):
                    user_data.update(meta)
                continue

            if len(parts) == 4 and parts[3].endswith(".json"):
                user_id, year, month, filename = parts
                day = filename[:-5]
                date_key = f"{year}-{month}-{day}"
                with zip_file.open(member) as src:
                    day_payload = json.loads(src.read().decode("utf-8"))
                user_data = legacy_data_by_user.setdefault(user_id, {})
                user_data[date_key] = day_payload

    summary = import_legacy_data_map(legacy_data_by_user)
    return {
        "storage_root": "zip-import",
        **summary,
    }
