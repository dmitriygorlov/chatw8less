import logging
import os
import json
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Mapping, Tuple

from bot.config import (
    DEFAULT_MODEL_MODE,
    LOG_DIR,
    MODEL_MODES,
    ROUND_STAT,
    STORAGE_DIR,
)
from bot.db import load_user_state, save_user_state
from bot.i18n import t


META_KEYS = {"daily_limit", "model_mode", "language_code"}


def _iter_day_entries(user_data):
    """Yield only date-like day payloads, skipping meta keys and malformed values."""
    for key, value in sorted(user_data.items()):
        if key in META_KEYS:
            continue
        if not isinstance(value, dict):
            continue
        yield key, value


def _safe_num(val):
    """
    Safeguard against errors (null, NaN, None, etc.).
    Converts to 0 if not a number.
    """
    return val if isinstance(val, (int, float)) else 0


def sanitize_items(items):
    """
    Checks a list of meal items and sets numeric fields to zero if they are None or missing.
    """
    for item in items:
        for field in ["amount_grams", "calories", "protein", "fat", "carbs"]:
            if field not in item or not isinstance(item[field], (int, float)):
                item[field] = 0
    return items


def round_stat(value: float, rounding: int = ROUND_STAT) -> float:
    """
    Rounds a float value to the specified number of decimal places.
    """
    return round(_safe_num(value), rounding)


def format_log_food_data(data: dict) -> str:
    """
    Formats structured meal data for display:
    - lists each item,
    - calculates and displays the total calories and macronutrients (protein, fat, carbs).
    """
    lines = []

    # Детализация по элементам
    items = sanitize_items(data.get("items", []))
    # сделаем заглушку, если items пустой
    if not items:
        return "No food data"

    total_cal = total_p = total_f = total_c = 0

    for item in items:
        name = item["name"]
        w = item["amount_grams"]
        cal = item["calories"]
        p = item["protein"]
        f = item["fat"]
        c = item["carbs"]
        lines.append(
            f"- {name} ({w} г): {cal} ккал, {p} г белков, {f} г жиров, {c} г углеводов"
        )
        # total_w += w
        total_cal += cal
        total_p += p
        total_f += f
        total_c += c

    # Итоговая строка
    lines.append("")  # пустая строка перед итогом
    lines.append(
        f"Общий итог: {round_stat(total_cal)} ккал, {round_stat(total_p)} г белков, {round_stat(total_f)} г жиров, {round_stat(total_c)} г углеводов"
    )

    return "\n".join(lines)


def _extract_usage_value(usage: Any, *names: str) -> Any:
    if usage is None:
        return None
    for name in names:
        if hasattr(usage, name):
            value = getattr(usage, name)
            if value is not None:
                return value
        if isinstance(usage, Mapping):
            value = usage.get(name)
            if value is not None:
                return value
    return None


def _truncate_log_text(text: str | None, limit: int = 400) -> str | None:
    if not text:
        return None
    sanitized = " ".join(text.split())
    if len(sanitized) <= limit:
        return sanitized
    return sanitized[: limit - 3] + "..."


def log_token_usage(
    usage: Any,
    user_id: int,
    finish_reason: str,
    tokens_log_file: str,
    function_name: str | None = None,
    model: str | None = None,
    input_text: str | None = None,
    output_text: str | None = None,
):
    """Persist token usage with optional snippets for auditing."""

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tokens_log_path = os.path.join(LOG_DIR, tokens_log_file)

    input_tokens = _extract_usage_value(usage, "prompt_tokens", "input_tokens")
    output_tokens = _extract_usage_value(usage, "completion_tokens", "output_tokens")
    total_tokens = _extract_usage_value(usage, "total_tokens")

    if total_tokens is None:
        in_val = input_tokens or 0
        out_val = output_tokens or 0
        total_tokens = in_val + out_val if (in_val or out_val) else None

    log_parts = [
        f"{timestamp} - User {user_id}",
        f"input_tokens: {input_tokens}",
        f"output_tokens: {output_tokens}",
        f"total: {total_tokens}",
        f"finish: {finish_reason}",
    ]

    if function_name:
        log_parts.append(f"function: {function_name}")
    if model:
        log_parts.append(f"model: {model}")

    truncated_input = _truncate_log_text(input_text)
    if truncated_input:
        log_parts.append(f"input: {truncated_input}")

    truncated_output = _truncate_log_text(output_text)
    if truncated_output:
        log_parts.append(f"output: {truncated_output}")

    log_line = ", ".join(log_parts) + "\n"

    with open(tokens_log_path, "a", encoding="utf-8") as f:
        f.write(log_line)

    logging.info(log_line.strip())


def format_day_statistics(user_data, date_str, title):
    day_data = user_data.get(date_str)
    if not day_data:
        return f"{title} нет записанных приёмов пищи."

    limit_cal = user_data.get("daily_limit")

    total_calories = total_protein = total_fat = total_carbs = 0
    msg = f"{title} ({date_str}):\n\n"
    for meal_num, items in day_data.items():
        items = sanitize_items(items)  # Ensure items are sanitized
        msg += f"🍽️ Приём пищи {meal_num}:\n"
        meal_calories = meal_protein = meal_fat = meal_carbs = 0
        for item in items:
            msg += (
                f" - {item['name']} ({round_stat(item['amount_grams'])} г): "
                f"{round_stat(item['calories'])} ккал, "
                f"Б {round_stat(item['protein'])} г, "
                f"Ж {round_stat(item['fat'])} г, "
                f"У {round_stat(item['carbs'])} г\n"
            )
            meal_calories += item["calories"]
            meal_protein += item["protein"]
            meal_fat += item["fat"]
            meal_carbs += item["carbs"]

        msg += (
            f"🔸 Всего за приём: {round_stat(meal_calories)} ккал, "
            f"Б {round_stat(meal_protein)} г, "
            f"Ж {round_stat(meal_fat)} г, "
            f"У {round_stat(meal_carbs)} г\n\n"
        )

        total_calories += meal_calories
        total_protein += meal_protein
        total_fat += meal_fat
        total_carbs += meal_carbs

    msg += "🔷 Итог за день:\n"
    msg += f"• Калории: {round_stat(total_calories)} ккал\n"
    if limit_cal:
        deficit = total_calories - limit_cal
        if deficit < 0:
            msg += f"🔥 Дефицит калорий: {round_stat(abs(deficit))} ккал\n"
        else:
            msg += f"⚠️ Избыток калорий: {round_stat(deficit)} ккал\n"
    msg += (
        f"• Белки: {round_stat(total_protein)} г\n"
        f"• Жиры: {round_stat(total_fat)} г\n"
        f"• Углеводы: {round_stat(total_carbs)} г"
    )

    return msg


def format_all_statistics(user_data):
    if not user_data:
        return "📊 У тебя ещё нет записанных приёмов пищи."

    limit_cal = user_data.get("daily_limit")
    day_entries = list(_iter_day_entries(user_data))
    if not day_entries:
        return "📊 У тебя ещё нет записанных приёмов пищи."

    days = 0

    msg = "📊 Вся твоя статистика:\n\n"
    grand_total_calories = grand_total_protein = grand_total_fat = grand_total_carbs = 0

    for date, day_data in day_entries:
        day_calories = day_protein = day_fat = day_carbs = 0
        msg += f"🗓 {date}:\n"
        for _, items in day_data.items():
            items = sanitize_items(items)  # Ensure items are sanitized
            meal_calories = sum(item["calories"] for item in items)
            meal_protein = sum(item["protein"] for item in items)
            meal_fat = sum(item["fat"] for item in items)
            meal_carbs = sum(item["carbs"] for item in items)

            day_calories += meal_calories
            day_protein += meal_protein
            day_fat += meal_fat
            day_carbs += meal_carbs

        msg += (
            f" 🔹 Итого: {round_stat(day_calories)} ккал, "
            f"Б {round_stat(day_protein)} г, "
            f"Ж {round_stat(day_fat)} г, "
            f"У {round_stat(day_carbs)} г\n\n"
        )
        days += 1
        grand_total_calories += day_calories
        grand_total_protein += day_protein
        grand_total_fat += day_fat
        grand_total_carbs += day_carbs

    msg += f"🔷 Общий итог за всё время ({days} дней):\n"
    msg += f"• Калории: {round_stat(grand_total_calories)} ккал\n"
    if limit_cal:
        deficit = grand_total_calories - limit_cal * days
        if deficit < 0:
            msg += f"🔥 Дефицит калорий: {round_stat(abs(deficit))} ккал\n"
        else:
            msg += f"⚠️ Избыток калорий: {round_stat(deficit)} ккал\n"
    msg += (
        f"• Белки: {round_stat(grand_total_protein)} г\n"
        f"• Жиры: {round_stat(grand_total_fat)} г\n"
        f"• Углеводы: {round_stat(grand_total_carbs)} г\n"
    )

    return msg


def format_last_7_days_statistics(user_data):
    """
    Aggregates stats for the last 7 full days, excluding today.
    Uses user's stored daily_limit if present to compute cumulative deficit/excess over the period.
    """
    if not user_data:
        return "📈 За последние 7 дней нет данных."

    limit_cal = user_data.get("daily_limit")

    # Build the set of target dates: yesterday back to 7 days ago
    today = datetime.now().date()
    dates = [
        (today - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(1, 8)
    ]

    # Filter only existing dates in user_data
    present_dates = [d for d in dates if d in user_data]
    if not present_dates:
        return "📈 За последние 7 дней нет записанных приёмов пищи."

    msg = "📈 Статистика за последние 7 дней (без сегодня):\n\n"
    total_days = 0
    grand_total_calories = grand_total_protein = grand_total_fat = grand_total_carbs = 0

    for date in sorted(present_dates):
        day_data = user_data.get(date, {})
        if not day_data:
            continue
        day_calories = day_protein = day_fat = day_carbs = 0
        msg += f"🗓 {date}:\n"
        for _, items in day_data.items():
            items = sanitize_items(items)
            day_calories += sum(item["calories"] for item in items)
            day_protein += sum(item["protein"] for item in items)
            day_fat += sum(item["fat"] for item in items)
            day_carbs += sum(item["carbs"] for item in items)

        msg += (
            f" 🔹 Итого: {round_stat(day_calories)} ккал, "
            f"Б {round_stat(day_protein)} г, "
            f"Ж {round_stat(day_fat)} г, "
            f"У {round_stat(day_carbs)} г\n\n"
        )
        total_days += 1
        grand_total_calories += day_calories
        grand_total_protein += day_protein
        grand_total_fat += day_fat
        grand_total_carbs += day_carbs

    msg += f"🔷 Общий итог за период ({total_days} дн.):\n"
    msg += f"• Калории: {round_stat(grand_total_calories)} ккал\n"
    if limit_cal and total_days:
        deficit = grand_total_calories - limit_cal * total_days
        if deficit < 0:
            msg += f"🔥 Дефицит калорий: {round_stat(abs(deficit))} ккал\n"
        else:
            msg += f"⚠️ Избыток калорий: {round_stat(deficit)} ккал\n"
    msg += (
        f"• Белки: {round_stat(grand_total_protein)} г\n"
        f"• Жиры: {round_stat(grand_total_fat)} г\n"
        f"• Углеводы: {round_stat(grand_total_carbs)} г\n"
    )

    return msg


# helpers for new storage structure
def _user_dir(user_id: str) -> str:
    """Возвращает путь к директории пользователя."""
    path = os.path.join(STORAGE_DIR, user_id)
    os.makedirs(path, exist_ok=True)
    return path


def _meta_file_path(user_id: str) -> str:
    """Путь к файлу с метаданными пользователя (лимит и т.д.)."""
    return os.path.join(_user_dir(user_id), "meta.json")


def _day_file_path(user_id: str, date_str: str, create_dirs: bool = True) -> str:
    """Путь к файлу с данными за конкретный день."""
    year, month, day = date_str.split("-")
    dir_path = os.path.join(_user_dir(user_id), year, month)
    if create_dirs:
        os.makedirs(dir_path, exist_ok=True)
    return os.path.join(dir_path, f"{day}.json")


def _cleanup_empty_dirs(path: str) -> None:
    """Удаляет пустые директории, двигаясь вверх до STORAGE_DIR."""
    while path.startswith(STORAGE_DIR) and path != STORAGE_DIR:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
            path = os.path.dirname(path)
        else:
            break


def _is_legacy_user_dir(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    if os.path.isfile(os.path.join(path, "meta.json")):
        return True
    for entry in os.listdir(path):
        entry_path = os.path.join(path, entry)
        if os.path.isdir(entry_path) and entry.isdigit() and len(entry) == 4:
            return True
    return False


def _find_legacy_storage_root(base_dir: str) -> str:
    normalized_base = os.path.abspath(base_dir)

    for root, dirs, _ in os.walk(normalized_base):
        depth = os.path.relpath(root, normalized_base).count(os.sep)
        if any(_is_legacy_user_dir(os.path.join(root, name)) for name in dirs):
            return root
        if depth >= 2:
            dirs[:] = []

    return normalized_base


def list_legacy_user_ids(storage_root: str | None = None) -> list[str]:
    root = _find_legacy_storage_root(storage_root or STORAGE_DIR)
    if not os.path.isdir(root):
        return []
    return sorted(
        entry
        for entry in os.listdir(root)
        if _is_legacy_user_dir(os.path.join(root, entry))
    )


def _load_user_data_from_files(user_id: str, storage_root: str | None = None) -> dict:
    """Load user data from legacy filesystem storage."""
    root = storage_root or STORAGE_DIR
    user_dir = os.path.join(root, user_id)
    if not os.path.exists(user_dir):
        return {}

    data: dict = {}

    # load meta info
    meta_path = os.path.join(user_dir, "meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
                data.update(meta)
        except json.JSONDecodeError:
            logging.error(f"Error loading meta data for {user_id}: JSON decode error")

    # iterate over year/month/day files
    for year in sorted(os.listdir(user_dir)):
        year_path = os.path.join(user_dir, year)
        if not os.path.isdir(year_path) or not year.isdigit():
            continue
        for month in sorted(os.listdir(year_path)):
            month_path = os.path.join(year_path, month)
            if not os.path.isdir(month_path) or not month.isdigit():
                continue
            for filename in sorted(os.listdir(month_path)):
                if not filename.endswith(".json"):
                    continue
                day = filename[:-5]
                date_key = f"{year}-{month}-{day}"
                file_path = os.path.join(month_path, filename)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data[date_key] = json.load(f)
                except json.JSONDecodeError:
                    logging.error(
                        f"Error loading day data for {user_id} {date_key}: JSON decode error"
                    )

    return data


def _merge_user_data(sqlite_data: dict, legacy_data: dict) -> dict:
    """Merge legacy file data into SQLite state without overwriting existing SQLite day payloads.

    Conflict rule:
    - day entries already present in SQLite win over legacy day files;
    - missing day entries are imported from legacy storage;
    - `daily_limit` is imported from legacy only if SQLite does not have it;
    - `model_mode` is imported from legacy only when SQLite still has only the default
      mode and no day entries yet (typical auto-created Telegram user row case).
    """
    merged = deepcopy(sqlite_data)
    sqlite_day_keys = {key for key in sqlite_data.keys() if key not in META_KEYS}
    legacy_day_keys = {key for key in legacy_data.keys() if key not in META_KEYS}

    if merged.get("daily_limit") is None and legacy_data.get("daily_limit") is not None:
        merged["daily_limit"] = legacy_data["daily_limit"]

    legacy_model_mode = legacy_data.get("model_mode")
    if legacy_model_mode:
        current_model_mode = merged.get("model_mode")
        if (
            current_model_mode not in MODEL_MODES
            or (not sqlite_day_keys and current_model_mode == DEFAULT_MODEL_MODE)
        ):
            merged["model_mode"] = legacy_model_mode

    for date_key in sorted(legacy_day_keys):
        if date_key not in sqlite_day_keys:
            merged[date_key] = legacy_data[date_key]

    return merged


def load_user_data_new(user_id: str) -> dict:
    """Load user data from SQLite and transparently merge legacy filesystem data."""
    data = load_user_state(user_id)
    legacy_data = _load_user_data_from_files(user_id)
    if not legacy_data:
        return data

    merged_data = _merge_user_data(data, legacy_data)
    if merged_data != data:
        save_user_state(user_id, merged_data)
    return merged_data


def save_user_data_new(user_id: str, data: dict) -> None:
    """Persist user data to SQLite-backed storage."""
    save_user_state(user_id, data)


def import_legacy_data_map(legacy_data_by_user: dict[str, dict]) -> dict:
    imported_user_ids: list[str] = []
    skipped_user_ids: list[str] = []

    for user_id, legacy_data in sorted(legacy_data_by_user.items()):
        if not legacy_data:
            skipped_user_ids.append(user_id)
            continue

        sqlite_data = load_user_state(user_id)
        merged_data = _merge_user_data(sqlite_data, legacy_data)

        if merged_data != sqlite_data:
            save_user_state(user_id, merged_data)
            imported_user_ids.append(user_id)
        else:
            skipped_user_ids.append(user_id)

    return {
        "imported_user_ids": imported_user_ids,
        "skipped_user_ids": skipped_user_ids,
    }


def import_legacy_storage(storage_root: str) -> dict:
    resolved_root = _find_legacy_storage_root(storage_root)
    legacy_data_by_user: dict[str, dict] = {}

    for user_id in list_legacy_user_ids(resolved_root):
        legacy_data = _load_user_data_from_files(user_id, storage_root=resolved_root)
        if legacy_data:
            legacy_data_by_user[user_id] = legacy_data

    summary = import_legacy_data_map(legacy_data_by_user)

    return {
        "storage_root": resolved_root,
        **summary,
    }


# Localized formatting overrides. They intentionally live after the legacy
# implementations so existing callers get translated output without a broad
# rewrite of the older helper body.
def format_log_food_data(data: dict, language_code: str | None = None) -> str:
    items = sanitize_items(data.get("items", []))
    if not items:
        return t(language_code, "common.no_data")

    total_cal = total_p = total_f = total_c = 0
    lines: list[str] = []
    grams = t(language_code, "common.grams")
    kcal = t(language_code, "common.calories")
    protein = t(language_code, "common.protein_short")
    fat = t(language_code, "common.fat_short")
    carbs = t(language_code, "common.carbs_short")

    for item in items:
        lines.append(
            f"- {item['name']} ({item['amount_grams']} {grams}): "
            f"{item['calories']} {kcal}, "
            f"{protein} {item['protein']} {grams}, "
            f"{fat} {item['fat']} {grams}, "
            f"{carbs} {item['carbs']} {grams}"
        )
        total_cal += item["calories"]
        total_p += item["protein"]
        total_f += item["fat"]
        total_c += item["carbs"]

    lines.append("")
    lines.append(
        f"{t(language_code, 'stats.total')}: "
        f"{round_stat(total_cal)} {kcal}, "
        f"{protein} {round_stat(total_p)} {grams}, "
        f"{fat} {round_stat(total_f)} {grams}, "
        f"{carbs} {round_stat(total_c)} {grams}"
    )
    return "\n".join(lines)


def _macro_line(
    language_code: str | None,
    calories: float,
    protein_value: float,
    fat_value: float,
    carbs_value: float,
) -> str:
    grams = t(language_code, "common.grams")
    return (
        f"{round_stat(calories)} {t(language_code, 'common.calories')}, "
        f"{t(language_code, 'common.protein_short')} {round_stat(protein_value)} {grams}, "
        f"{t(language_code, 'common.fat_short')} {round_stat(fat_value)} {grams}, "
        f"{t(language_code, 'common.carbs_short')} {round_stat(carbs_value)} {grams}"
    )


def format_day_statistics(user_data, date_str, title, language_code: str | None = None):
    day_data = user_data.get(date_str)
    if not day_data:
        return t(language_code, "stats.no_meals_day", title=title)

    limit_cal = user_data.get("daily_limit")
    total_calories = total_protein = total_fat = total_carbs = 0
    msg = f"{title} ({date_str}):\n\n"

    for meal_num, items in day_data.items():
        items = sanitize_items(items)
        msg += f"🍽️ {t(language_code, 'stats.meal')} {meal_num}:\n"
        meal_calories = meal_protein = meal_fat = meal_carbs = 0
        for item in items:
            msg += (
                f" - {item['name']} ({round_stat(item['amount_grams'])} {t(language_code, 'common.grams')}): "
                f"{_macro_line(language_code, item['calories'], item['protein'], item['fat'], item['carbs'])}\n"
            )
            meal_calories += item["calories"]
            meal_protein += item["protein"]
            meal_fat += item["fat"]
            meal_carbs += item["carbs"]

        msg += f"🔸 {t(language_code, 'stats.total')}: {_macro_line(language_code, meal_calories, meal_protein, meal_fat, meal_carbs)}\n\n"
        total_calories += meal_calories
        total_protein += meal_protein
        total_fat += meal_fat
        total_carbs += meal_carbs

    msg += f"🔷 {t(language_code, 'stats.day_total')}:\n"
    msg += f"• {t(language_code, 'stats.calories')}: {round_stat(total_calories)} {t(language_code, 'common.calories')}\n"
    if limit_cal:
        deficit = total_calories - limit_cal
        if deficit < 0:
            msg += f"🔥 {t(language_code, 'stats.limit_deficit')}: {round_stat(abs(deficit))} {t(language_code, 'common.calories')}\n"
        else:
            msg += f"⚠️ {t(language_code, 'stats.excess')}: {round_stat(deficit)} {t(language_code, 'common.calories')}\n"
    msg += (
        f"• {t(language_code, 'common.protein_short')}: {round_stat(total_protein)} {t(language_code, 'common.grams')}\n"
        f"• {t(language_code, 'common.fat_short')}: {round_stat(total_fat)} {t(language_code, 'common.grams')}\n"
        f"• {t(language_code, 'common.carbs_short')}: {round_stat(total_carbs)} {t(language_code, 'common.grams')}"
    )
    return msg


def format_all_statistics(user_data, language_code: str | None = None):
    day_entries = list(_iter_day_entries(user_data or {}))
    if not day_entries:
        return f"📊 {t(language_code, 'stats.no_meals_all')}"

    limit_cal = user_data.get("daily_limit")
    msg = f"📊 {t(language_code, 'stats.all_title')}:\n\n"
    days = 0
    grand_total_calories = grand_total_protein = grand_total_fat = grand_total_carbs = 0

    for date, day_data in day_entries:
        day_calories = day_protein = day_fat = day_carbs = 0
        msg += f"🗓 {date}:\n"
        for _, items in day_data.items():
            items = sanitize_items(items)
            day_calories += sum(item["calories"] for item in items)
            day_protein += sum(item["protein"] for item in items)
            day_fat += sum(item["fat"] for item in items)
            day_carbs += sum(item["carbs"] for item in items)
        msg += f" 🔹 {t(language_code, 'stats.total')}: {_macro_line(language_code, day_calories, day_protein, day_fat, day_carbs)}\n\n"
        days += 1
        grand_total_calories += day_calories
        grand_total_protein += day_protein
        grand_total_fat += day_fat
        grand_total_carbs += day_carbs

    msg += f"🔷 {t(language_code, 'stats.total_all', days=days)}:\n"
    msg += f"• {t(language_code, 'stats.calories')}: {round_stat(grand_total_calories)} {t(language_code, 'common.calories')}\n"
    if limit_cal:
        deficit = grand_total_calories - limit_cal * days
        if deficit < 0:
            msg += f"🔥 {t(language_code, 'stats.limit_deficit')}: {round_stat(abs(deficit))} {t(language_code, 'common.calories')}\n"
        else:
            msg += f"⚠️ {t(language_code, 'stats.excess')}: {round_stat(deficit)} {t(language_code, 'common.calories')}\n"
    msg += (
        f"• {t(language_code, 'common.protein_short')}: {round_stat(grand_total_protein)} {t(language_code, 'common.grams')}\n"
        f"• {t(language_code, 'common.fat_short')}: {round_stat(grand_total_fat)} {t(language_code, 'common.grams')}\n"
        f"• {t(language_code, 'common.carbs_short')}: {round_stat(grand_total_carbs)} {t(language_code, 'common.grams')}\n"
    )
    return msg


def format_last_7_days_statistics(user_data, language_code: str | None = None):
    if not user_data:
        return f"📈 {t(language_code, 'stats.last7_no_data')}"

    limit_cal = user_data.get("daily_limit")
    today = datetime.now().date()
    dates = [
        (today - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(1, 8)
    ]
    present_dates = [date for date in dates if date in user_data]
    if not present_dates:
        return f"📈 {t(language_code, 'stats.last7_empty')}"

    msg = f"📈 {t(language_code, 'stats.last7_title')}:\n\n"
    total_days = 0
    grand_total_calories = grand_total_protein = grand_total_fat = grand_total_carbs = 0

    for date in sorted(present_dates):
        day_data = user_data.get(date, {})
        if not day_data:
            continue
        day_calories = day_protein = day_fat = day_carbs = 0
        msg += f"🗓 {date}:\n"
        for _, items in day_data.items():
            items = sanitize_items(items)
            day_calories += sum(item["calories"] for item in items)
            day_protein += sum(item["protein"] for item in items)
            day_fat += sum(item["fat"] for item in items)
            day_carbs += sum(item["carbs"] for item in items)
        msg += f" 🔹 {t(language_code, 'stats.total')}: {_macro_line(language_code, day_calories, day_protein, day_fat, day_carbs)}\n\n"
        total_days += 1
        grand_total_calories += day_calories
        grand_total_protein += day_protein
        grand_total_fat += day_fat
        grand_total_carbs += day_carbs

    msg += f"🔷 {t(language_code, 'stats.period_total', days=total_days)}:\n"
    msg += f"• {t(language_code, 'stats.calories')}: {round_stat(grand_total_calories)} {t(language_code, 'common.calories')}\n"
    if limit_cal and total_days:
        deficit = grand_total_calories - limit_cal * total_days
        if deficit < 0:
            msg += f"🔥 {t(language_code, 'stats.limit_deficit')}: {round_stat(abs(deficit))} {t(language_code, 'common.calories')}\n"
        else:
            msg += f"⚠️ {t(language_code, 'stats.excess')}: {round_stat(deficit)} {t(language_code, 'common.calories')}\n"
    msg += (
        f"• {t(language_code, 'common.protein_short')}: {round_stat(grand_total_protein)} {t(language_code, 'common.grams')}\n"
        f"• {t(language_code, 'common.fat_short')}: {round_stat(grand_total_fat)} {t(language_code, 'common.grams')}\n"
        f"• {t(language_code, 'common.carbs_short')}: {round_stat(grand_total_carbs)} {t(language_code, 'common.grams')}\n"
    )
    return msg


def resolve_model_mode(mode: str) -> dict:
    """Return model configuration for provided mode with fallback."""
    return MODEL_MODES.get(mode, MODEL_MODES[DEFAULT_MODEL_MODE])


def get_user_model_mode(user_id: str) -> str:
    """Ensure user has a stored model mode and return it."""
    data = load_user_data_new(user_id)
    mode = data.get("model_mode")
    if mode not in MODEL_MODES:
        mode = DEFAULT_MODEL_MODE
        data["model_mode"] = mode
        save_user_data_new(user_id, data)
    return mode


def get_user_model_config(user_id: str) -> tuple[str, dict]:
    """Return tuple of (mode_key, model_config) for user."""
    mode = get_user_model_mode(user_id)
    return mode, resolve_model_mode(mode)


def set_user_model_mode(user_id: str, mode: str) -> str:
    """Persist new model mode for user and return normalized mode."""
    normalized_mode = mode.lower()
    if normalized_mode not in MODEL_MODES:
        raise ValueError(f"Unsupported model mode: {mode}")
    data = load_user_data_new(user_id)
    data["model_mode"] = normalized_mode
    save_user_data_new(user_id, data)
    return normalized_mode


def extract_function_call_payload(response) -> Tuple[Any, str | None, str | None]:
    """Return (payload, function_name, status) from a Responses API result."""
    outputs = getattr(response, "output", None) or []

    for item in outputs:
        item_type = getattr(item, "type", None)
        if item_type == "function_call":
            arguments = getattr(item, "arguments", None)
            if arguments is None and getattr(item, "function_call", None):
                arguments = item.function_call.arguments
            return arguments, getattr(item, "name", None), getattr(item, "status", None)

    for item in outputs:
        item_type = getattr(item, "type", None)
        if item_type == "message":
            for content in getattr(item, "content", None) or []:
                if getattr(content, "type", None) == "output_text":
                    return (
                        content.text,
                        getattr(item, "name", None),
                        getattr(item, "status", None),
                    )

    text = getattr(response, "output_text", None)
    if text:
        return text, None, getattr(response, "status", None)

    raise ValueError("No payload found in response output")


def get_response_finish_reason(response) -> str:
    """Derive a finish reason/status from the Responses API result."""
    outputs = getattr(response, "output", None) or []
    for item in reversed(outputs):
        status = getattr(item, "status", None)
        if status:
            return status
    return getattr(response, "status", None) or "unknown"


# ---------------------------- Messaging helpers ---------------------------- #

# Telegram hard limit for one text message
TELEGRAM_MESSAGE_LIMIT = 4096


def split_message(text: str, max_length: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split text into chunks that fit Telegram's limit.

    Strategy:
    - Try to split by lines to keep structure.
    - If a single line is too long, hard-split it by max_length.
    """
    if text is None:
        return [""]

    chunks: list[str] = []
    current: str = ""

    for line in text.splitlines(keepends=True):
        # If the line itself is longer than max_length, break it hard
        while len(line) > max_length:
            head, line = line[:max_length], line[max_length:]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)

        # Now the line fits
        if len(current) + len(line) > max_length:
            if current:
                chunks.append(current)
            current = line
        else:
            current += line

    if current:
        chunks.append(current)

    # Ensure no empty chunks
    return [c for c in chunks if c]


async def send_long_message(
    message_obj,
    text: str,
    method: str = "answer",
    reply_markup=None,
    parse_mode: str | None = None,
    disable_web_page_preview: bool | None = None,
    **kwargs,
):
    """Send a long text safely by splitting into chunks.

    - message_obj: aiogram.types.Message (or any object with .answer/.reply)
    - method: "answer" or "reply"; defaults to "answer"
    - reply_markup: applied only to the first chunk to avoid duplicated keyboards
    - parse_mode/disable_web_page_preview forwarded if provided
    - kwargs: forwarded to the underlying send method
    """
    send = getattr(message_obj, method)

    parts = split_message(text, TELEGRAM_MESSAGE_LIMIT)
    first = True
    last_msg = None
    for part in parts:
        extra = dict(kwargs)
        if parse_mode is not None:
            extra["parse_mode"] = parse_mode
        if disable_web_page_preview is not None:
            extra["disable_web_page_preview"] = disable_web_page_preview
        if first and reply_markup is not None:
            extra["reply_markup"] = reply_markup
        else:
            # Avoid sending the same keyboard with every chunk
            extra.pop("reply_markup", None)

        last_msg = await send(part, **extra)
        first = False
    return last_msg
