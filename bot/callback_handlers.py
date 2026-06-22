import json
import logging
from datetime import datetime, timedelta

from aiogram import F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot.config import HOUR_SHIFT, MODEL_MODES, TOKENS_LOG_FILE
from bot.chat_service import process_text_interaction
from bot.db import get_user_language
from bot.i18n import t
from bot.openai_client import get_openai_response
from bot.states import EditNutritionState, LimitDataState, SaveNutritionData
from bot.utils import (
    format_all_statistics,
    format_day_statistics,
    format_last_7_days_statistics,
    load_user_data_new,
    save_user_data_new,
    sanitize_items,
    send_long_message,
    get_user_model_config,
    set_user_model_mode,
    META_KEYS,
)


async def save_confirmation(callback: types.CallbackQuery, state: FSMContext):
    user_id = str(callback.from_user.id)

    if callback.data == "save_yes":
        data = await state.get_data()
        nutrition_data = data.get("nutrition_data", {})

        items = nutrition_data.get("items")
        if not isinstance(items, list):
            await callback.message.answer("⚠️ Ошибка: данные некорректны.")
            return

        items = sanitize_items(items)

        datetime_now = datetime.now()
        # смещаем дату на HOUR_SHIFT
        datetime_now_shifted = datetime_now + timedelta(hours=HOUR_SHIFT)
        today = datetime_now_shifted.strftime("%Y-%m-%d")
        user_data = load_user_data_new(user_id)

        date_meals = user_data.setdefault(today, {})

        # try to find the next available meal number
        existing_numbers = [int(k) for k in date_meals.keys() if k.isdigit()]
        next_meal_number = max(existing_numbers, default=0) + 1

        date_meals[str(next_meal_number)] = items

        try:
            save_user_data_new(user_id, user_data)
        except Exception as e:
            await callback.message.answer(f"⚠️ Не удалось сохранить данные: {e}")
            return

        await callback.message.answer(
            "✅ Данные успешно сохранены как новый приём пищи!"
        )

    else:
        await callback.message.answer("❌ Хорошо, не сохраняем.")

    await callback.message.edit_reply_markup(reply_markup=None)
    await state.clear()


async def stats_callback(callback: types.CallbackQuery):
    user_id = str(callback.from_user.id)
    user_data = load_user_data_new(user_id)

    period = callback.data

    if period == "stats_today":
        date_str = datetime.now().strftime("%Y-%m-%d")
        message_text = format_day_statistics(user_data, date_str, "📅 Сегодня")
    elif period == "stats_yesterday":
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        message_text = format_day_statistics(user_data, date_str, "📆 Вчера")
    elif period == "stats_7days":
        message_text = format_last_7_days_statistics(user_data)
    elif period == "stats_all":
        message_text = format_all_statistics(user_data)
    else:
        message_text = "⚠️ Неизвестная команда статистики."

    await callback.message.edit_reply_markup(reply_markup=None)
    # Use safe long-message sender for potentially large stats
    await send_long_message(callback.message, message_text)


async def limit_menu_callback(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора в меню /limits."""
    user_id = str(callback.from_user.id)
    user_data = load_user_data_new(user_id)

    choice = callback.data

    if choice == "limit_set":
        await callback.message.answer("Введите новый дневной лимит калорий:")
        await state.set_state(LimitDataState.waiting_for_limit_value)
    elif choice == "limit_view":
        # Get the total consumed calories for today
        today = datetime.now().strftime("%Y-%m-%d")
        today_data = user_data.get(today, {})

        total_consumed = (
            sum(item["calories"] for meal in today_data.values() for item in meal)
            if today_data
            else 0
        )

        limit = user_data.get("daily_limit")
        if limit:
            deficit = total_consumed - limit
            await callback.message.answer(
                f"На сегодня ({today}) лимит составляет {limit} ккал.\n"
                f"Уже потреблено: {total_consumed} ккал.\n"
                f"Осталось до лимита: {deficit} ккал."
            )
        else:
            await callback.message.answer(
                "Дневной лимит ещё не задан. Укажите его через /limits."
            )
        await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)


async def edit_year_callback(callback: types.CallbackQuery, state: FSMContext):
    _, _, year = callback.data.split(":", maxsplit=2)
    await state.update_data(selected_year=year)

    user_id = str(callback.from_user.id)
    user_data = load_user_data_new(user_id)
    months = sorted({d.split("-")[1] for d in user_data if d.startswith(year)})

    buttons = [
        [InlineKeyboardButton(text=m, callback_data=f"edit:month:{year}-{m}")]
        for m in months
    ]
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="edit:back_to_year")]
    )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(f"Год {year}. Выберите месяц:", reply_markup=kb)
    await state.set_state(EditNutritionState.waiting_for_month)


async def edit_back_to_year(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()

    user_id = str(callback.from_user.id)
    user_data = load_user_data_new(user_id)
    dates = [d for d in user_data.keys() if d not in META_KEYS]
    years = sorted({d.split("-")[0] for d in dates})

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=y, callback_data=f"edit:year:{y}")]
            for y in years
        ]
    )

    await callback.message.edit_text(
        "Выберите год для редактирования:", reply_markup=kb
    )
    await state.set_state(EditNutritionState.waiting_for_year)


async def edit_month_callback(callback: types.CallbackQuery, state: FSMContext):
    _, _, ym = callback.data.split(":", maxsplit=2)
    year, month = ym.split("-")
    await state.update_data(selected_year=year, selected_month=month)

    user_id = str(callback.from_user.id)
    user_data = load_user_data_new(user_id)
    days = sorted(
        {d.split("-")[2] for d in user_data if d.startswith(f"{year}-{month}")}
    )

    buttons = [
        [InlineKeyboardButton(text=d, callback_data=f"edit:date:{year}-{month}-{d}")]
        for d in days
    ]
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="edit:back_to_month")]
    )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(f"{year}-{month}. Выберите день:", reply_markup=kb)
    await state.set_state(EditNutritionState.waiting_for_day)


async def edit_back_to_month(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    year = data.get("selected_year")

    user_id = str(callback.from_user.id)
    user_data = load_user_data_new(user_id)
    months = sorted({d.split("-")[1] for d in user_data if d.startswith(year)})

    buttons = [
        [InlineKeyboardButton(text=m, callback_data=f"edit:month:{year}-{m}")]
        for m in months
    ]
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="edit:back_to_year")]
    )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(f"Год {year}. Выберите месяц:", reply_markup=kb)
    await state.set_state(EditNutritionState.waiting_for_month)


async def edit_date_callback(callback: types.CallbackQuery, state: FSMContext):
    # Из callback_data формата "edit:date:2025-04-05"
    _, _, selected_date = callback.data.split(":", maxsplit=2)
    await state.update_data(selected_date=selected_date)

    user_id = str(callback.from_user.id)
    user_data = load_user_data_new(user_id)
    meals = user_data.get(selected_date, {})

    buttons = [
        [InlineKeyboardButton(text=f"Приём {m}", callback_data=f"edit:meal:{m}")]
        for m in sorted(meals.keys(), key=int)
    ]
    # Добавляем кнопку «Назад»
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="edit:back_to_date")]
    )
    # Инициализируем клавиатуру
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        f"Дата {selected_date}. Выберите приём или Назад:", reply_markup=kb
    )
    await state.set_state(EditNutritionState.waiting_for_meal)


async def edit_back_to_date(callback: types.CallbackQuery, state: FSMContext):
    # 1. Убираем «крутилку» у клиента
    await callback.answer()

    data = await state.get_data()
    year = data.get("selected_year")
    month = data.get("selected_month")

    # 2. Подгружаем дни из данных пользователя
    user_id = str(callback.from_user.id)
    user_data = load_user_data_new(user_id)
    days = sorted(
        {d.split("-")[2] for d in user_data if d.startswith(f"{year}-{month}")}
    )

    # 3. Строим новую клавиатуру
    buttons = [
        [InlineKeyboardButton(text=d, callback_data=f"edit:date:{year}-{month}-{d}")]
        for d in days
    ]
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="edit:back_to_month")]
    )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    # 4. Редактируем тот же месседж, а не отправляем новый
    await callback.message.edit_text(f"{year}-{month}. Выберите день:", reply_markup=kb)

    # 5. Устанавливаем стейт обратно в выбор дня
    await state.set_state(EditNutritionState.waiting_for_day)


async def edit_meal_callback(callback: types.CallbackQuery, state: FSMContext):
    _, _, meal_no = callback.data.split(":", maxsplit=2)
    await state.update_data(selected_meal=meal_no)

    data = await state.get_data()
    user_id = str(callback.from_user.id)
    user_data = load_user_data_new(user_id)
    items = user_data[data["selected_date"]][meal_no]

    buttons = [
        [
            InlineKeyboardButton(
                text=f"{idx+1}. {item['name']}", callback_data=f"edit:item:{idx}"
            )
        ]
        for idx, item in enumerate(items)
    ]
    # Добавляем кнопки для удаления приёма и возврата назад
    buttons.append(
        [
            InlineKeyboardButton(
                text="🗑 Удалить весь приём", callback_data="edit:delete_meal"
            )
        ]
    )
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="edit:back_to_meals")]
    )
    # Инициализируем клавиатуру
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(
        f"Приём {meal_no} в дату {data['selected_date']}. Выберите позицию, удалить приём или Назад:",
        reply_markup=kb,
    )
    await state.set_state(EditNutritionState.waiting_for_item)


async def edit_back_to_meals(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    selected_date = data["selected_date"]

    # заново собираем клавиатуру дат
    user_id = str(callback.from_user.id)
    user_data = load_user_data_new(user_id)
    meals = user_data.get(selected_date, {})

    buttons = [
        [InlineKeyboardButton(text=f"Приём {m}", callback_data=f"edit:meal:{m}")]
        for m in sorted(meals.keys(), key=int)
    ]
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="edit:back_to_date")]
    )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        f"Дата {selected_date}. Выберите приём или Назад:", reply_markup=kb
    )
    await state.set_state(EditNutritionState.waiting_for_meal)


async def edit_delete_meal(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()

    data = await state.get_data()
    user_id = str(callback.from_user.id)
    user_data = load_user_data_new(user_id)

    date_key = data["selected_date"]
    meal_no = data["selected_meal"]

    # Удаляем
    user_data[date_key].pop(meal_no, None)
    save_user_data_new(user_id, user_data)

    # Собираем обновлённый список приёмов
    meals = user_data.get(date_key, {})
    if not meals:
        # Если больше нет ни одного приёма — возвращаем на уровень выбора даты
        await callback.message.edit_text("Больше нет приёмов на эту дату.")
        await state.set_state(EditNutritionState.waiting_for_day)
        return await edit_back_to_date(callback, state)

    buttons = [
        [InlineKeyboardButton(text=f"Приём {m}", callback_data=f"edit:meal:{m}")]
        for m in sorted(meals.keys(), key=int)
    ]
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="edit:back_to_date")]
    )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        f"Дата {date_key}. Приём {meal_no} удалён на дату {date_key}.\n Выберите другой приём или Назад:",
        reply_markup=kb,
    )
    await state.set_state(EditNutritionState.waiting_for_meal)


async def edit_item_callback(callback: types.CallbackQuery, state: FSMContext):
    _, _, idx = callback.data.split(":", maxsplit=2)
    await state.update_data(selected_item=int(idx))

    buttons = [
        [
            InlineKeyboardButton(
                text="🗑 Удалить позицию", callback_data="edit:action:delete_item"
            )
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="edit:back_to_items")],
    ]
    # Инициализируем клавиатуру
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(
        "Выберите действие для этой позиции:", reply_markup=kb
    )
    await state.set_state(EditNutritionState.waiting_for_action)


async def edit_back_to_items(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    selected_date = data["selected_date"]
    selected_meal = data["selected_meal"]

    # заново собираем клавиатуру позиций
    user_id = str(callback.from_user.id)
    user_data = load_user_data_new(user_id)
    items = user_data[selected_date][selected_meal]

    buttons = [
        [
            InlineKeyboardButton(
                text=f"{idx+1}. {item['name']}", callback_data=f"edit:item:{idx}"
            )
        ]
        for idx, item in enumerate(items)
    ]
    buttons.append(
        [
            InlineKeyboardButton(
                text="🗑 Удалить весь приём", callback_data="edit:delete_meal"
            )
        ]
    )
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="edit:back_to_meals")]
    )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        f"Приём {selected_meal}. Выберите позицию, удалить приём или Назад:",
        reply_markup=kb,
    )
    await state.set_state(EditNutritionState.waiting_for_item)


async def edit_delete_item(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()

    data = await state.get_data()
    user_id = str(callback.from_user.id)
    user_data = load_user_data_new(user_id)

    date_key = data["selected_date"]
    meal_no = data["selected_meal"]
    idx = data["selected_item"]

    items = user_data[date_key][meal_no]
    items.pop(idx)
    save_user_data_new(user_id, user_data)

    # Собираем обновлённый список позиций
    if not items:
        # Если в приёме не осталось позиций — возвращаемся к списку приёмов
        await callback.message.edit_text(f"Все позиции из приёма {meal_no} удалены.")
        await state.clear()  # или set_state(waiting_for_day) + edit_back_to_date
        return await edit_back_to_date(callback, state)

    buttons = [
        [
            InlineKeyboardButton(
                text=f"{i+1}. {item['name']}", callback_data=f"edit:item:{i}"
            )
        ]
        for i, item in enumerate(items)
    ]
    buttons.append(
        [
            InlineKeyboardButton(
                text="🗑 Удалить весь приём", callback_data="edit:delete_meal"
            )
        ]
    )
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="edit:back_to_meals")]
    )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        f"Позиция {idx+1} удалена из приёма {meal_no} на дату {date_key}.\n Выберите другую позицию, удалить приём или Назад:",
        reply_markup=kb,
    )
    await state.set_state(EditNutritionState.waiting_for_item)


async def model_set_callback(callback: types.CallbackQuery):
    try:
        _, _, mode_key = callback.data.split(":", maxsplit=2)
    except ValueError:
        await callback.answer("⚠️ Не удалось определить режим.")
        await callback.message.answer("⚠️ Не удалось определить выбранный режим.")
        return

    user_id = str(callback.from_user.id)

    try:
        normalized_mode = set_user_model_mode(user_id, mode_key)
    except ValueError:
        await callback.answer("⚠️ Неизвестный режим модели.")
        await callback.message.answer("⚠️ Неизвестный режим модели.")
        return

    _, model_config = get_user_model_config(user_id)
    await callback.answer(f"Выбрано: {model_config['label']}")

    buttons = []
    for key, info in MODEL_MODES.items():
        prefix = "✅ " if key == normalized_mode else ""
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix}{info['label']}",
                    callback_data=f"model:set:{key}",
                )
            ]
        )
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    text = (
        "Режим модели обновлён.\n"
        f"Теперь используется: {model_config['label']} ({model_config['model']})."
    )

    await callback.message.edit_text(text, reply_markup=keyboard)


async def vision_evaluate_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()

    data = await state.get_data()
    candidate = data.get("vision_candidate") or {}
    base_text = candidate.get("text")

    if not base_text:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Не найдено данных для оценки. Пришлите фото ещё раз."
        )
        return

    caption = candidate.get("caption") or ""
    evaluation_input = base_text if not caption else f"{caption}\n{base_text}"

    user_id = str(callback.from_user.id)
    mode, model_config = get_user_model_config(user_id)

    try:
        interaction = await process_text_interaction(
            user_id=user_id,
            user_text=evaluation_input,
            source="telegram",
            auto_save_meal=False,
        )
    except Exception as exc:
        logging.error(
            "Error during vision calorie evaluation for user %s: %s",
            user_id,
            exc,
        )
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Произошла ошибка при оценке. Попробуйте снова позже."
        )
        return

    final_answer = interaction["reply_text"]

    await state.update_data(
        nutrition_data={"items": interaction["items"]},
        vision_candidate=None,
    )
    await state.set_state(SaveNutritionData.waiting_for_confirmation)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Сохранить", callback_data="save_yes"),
                InlineKeyboardButton(text="❌ Не надо", callback_data="save_no"),
            ]
        ]
    )

    await callback.message.edit_reply_markup(reply_markup=None)

    await send_long_message(
        callback.message,
        final_answer,
        reply_markup=keyboard,
    )


# ---------------------------- Localized callback overrides ---------------------------- #

async def save_confirmation(callback: types.CallbackQuery, state: FSMContext):
    user_id = str(callback.from_user.id)
    language_code = get_user_language(user_id)

    if callback.data == "save_yes":
        data = await state.get_data()
        nutrition_data = data.get("nutrition_data", {})
        items = nutrition_data.get("items")
        if not isinstance(items, list):
            await callback.message.answer(t(language_code, "telegram.save.error"))
            return

        items = sanitize_items(items)
        today = (datetime.now() + timedelta(hours=HOUR_SHIFT)).strftime("%Y-%m-%d")
        user_data = load_user_data_new(user_id)
        date_meals = user_data.setdefault(today, {})
        existing_numbers = [int(k) for k in date_meals.keys() if k.isdigit()]
        next_meal_number = max(existing_numbers, default=0) + 1
        date_meals[str(next_meal_number)] = items

        try:
            save_user_data_new(user_id, user_data)
        except Exception as e:
            await callback.message.answer(t(language_code, "telegram.save.failure", error=e))
            return

        await callback.message.answer(t(language_code, "telegram.save.success"))
    else:
        await callback.message.answer(t(language_code, "telegram.save.no"))

    await callback.message.edit_reply_markup(reply_markup=None)
    await state.clear()


async def stats_callback(callback: types.CallbackQuery):
    user_id = str(callback.from_user.id)
    language_code = get_user_language(user_id)
    user_data = load_user_data_new(user_id)
    period = callback.data

    if period == "stats_today":
        date_str = datetime.now().strftime("%Y-%m-%d")
        message_text = format_day_statistics(
            user_data, date_str, f"📅 {t(language_code, 'stats.today')}", language_code
        )
    elif period == "stats_yesterday":
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        message_text = format_day_statistics(
            user_data, date_str, f"📆 {t(language_code, 'stats.yesterday')}", language_code
        )
    elif period == "stats_7days":
        message_text = format_last_7_days_statistics(user_data, language_code)
    elif period == "stats_all":
        message_text = format_all_statistics(user_data, language_code)
    else:
        message_text = t(language_code, "telegram.stats.unknown")

    await callback.message.edit_reply_markup(reply_markup=None)
    await send_long_message(callback.message, message_text)


async def limit_menu_callback(callback: types.CallbackQuery, state: FSMContext):
    user_id = str(callback.from_user.id)
    language_code = get_user_language(user_id)
    user_data = load_user_data_new(user_id)

    if callback.data == "limit_set":
        await callback.message.answer(t(language_code, "telegram.limit.set_prompt"))
        await state.set_state(LimitDataState.waiting_for_limit_value)
    elif callback.data == "limit_view":
        today = datetime.now().strftime("%Y-%m-%d")
        today_data = user_data.get(today, {})
        total_consumed = (
            sum(item["calories"] for meal in today_data.values() for item in meal)
            if today_data
            else 0
        )
        limit = user_data.get("daily_limit")
        if limit:
            remaining = limit - total_consumed
            await callback.message.answer(
                t(
                    language_code,
                    "telegram.limit.view_result",
                    date=today,
                    limit=limit,
                    consumed=total_consumed,
                    remaining=remaining,
                )
            )
        else:
            await callback.message.answer(t(language_code, "telegram.limit.view_empty"))
        await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)


async def model_set_callback(callback: types.CallbackQuery):
    language_code = get_user_language(str(callback.from_user.id))
    try:
        _, _, mode_key = callback.data.split(":", maxsplit=2)
    except ValueError:
        await callback.answer(t(language_code, "telegram.model.parse_error"))
        await callback.message.answer(t(language_code, "telegram.model.parse_error"))
        return

    user_id = str(callback.from_user.id)
    try:
        normalized_mode = set_user_model_mode(user_id, mode_key)
    except ValueError:
        await callback.answer(t(language_code, "telegram.model.invalid"))
        await callback.message.answer(t(language_code, "telegram.model.invalid"))
        return

    _, model_config = get_user_model_config(user_id)
    label = t(language_code, f"mode.{normalized_mode}")
    await callback.answer(label)

    buttons = []
    for key, info in MODEL_MODES.items():
        prefix = "✅ " if key == normalized_mode else ""
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix}{t(language_code, f'mode.{key}')}",
                    callback_data=f"model:set:{key}",
                )
            ]
        )

    await callback.message.edit_text(
        t(language_code, "telegram.model.updated", label=label, model=model_config["model"]),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


async def vision_evaluate_callback(callback: types.CallbackQuery, state: FSMContext):
    language_code = get_user_language(str(callback.from_user.id))
    await callback.answer()
    data = await state.get_data()
    candidate = data.get("vision_candidate") or {}
    base_text = candidate.get("text")

    if not base_text:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(t(language_code, "telegram.site.no_candidate"))
        return

    caption = candidate.get("caption") or ""
    evaluation_input = base_text if not caption else f"{caption}\n{base_text}"
    user_id = str(callback.from_user.id)

    try:
        interaction = await process_text_interaction(
            user_id=user_id,
            user_text=evaluation_input,
            source="telegram",
            auto_save_meal=False,
        )
    except Exception as exc:
        logging.error("Error during vision calorie evaluation for user %s: %s", user_id, exc)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(t(language_code, "telegram.vision.evaluation_error"))
        return

    await state.update_data(nutrition_data={"items": interaction["items"]}, vision_candidate=None)
    await state.set_state(SaveNutritionData.waiting_for_confirmation)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t(language_code, "telegram.save.yes"), callback_data="save_yes"),
                InlineKeyboardButton(text=t(language_code, "telegram.no"), callback_data="save_no"),
            ]
        ]
    )
    await callback.message.edit_reply_markup(reply_markup=None)
    await send_long_message(callback.message, interaction["reply_text"], reply_markup=keyboard)


def _back_button(language_code: str, callback_data: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=f"⬅️ {t(language_code, 'common.back')}", callback_data=callback_data)]


async def edit_year_callback(callback: types.CallbackQuery, state: FSMContext):
    language_code = get_user_language(str(callback.from_user.id))
    _, _, year = callback.data.split(":", maxsplit=2)
    await state.update_data(selected_year=year)
    user_data = load_user_data_new(str(callback.from_user.id))
    months = sorted({date.split("-")[1] for date in user_data if date.startswith(year)})
    buttons = [
        [InlineKeyboardButton(text=month, callback_data=f"edit:month:{year}-{month}")]
        for month in months
    ]
    buttons.append(_back_button(language_code, "edit:back_to_year"))
    await callback.message.edit_text(
        t(language_code, "telegram.edit.choose_month", year=year),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(EditNutritionState.waiting_for_month)


async def edit_back_to_year(callback: types.CallbackQuery, state: FSMContext):
    language_code = get_user_language(str(callback.from_user.id))
    await callback.answer()
    user_data = load_user_data_new(str(callback.from_user.id))
    dates = [date for date in user_data.keys() if date not in META_KEYS]
    years = sorted({date.split("-")[0] for date in dates})
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=year, callback_data=f"edit:year:{year}")]
            for year in years
        ]
    )
    await callback.message.edit_text(
        t(language_code, "telegram.edit.choose_year"),
        reply_markup=keyboard,
    )
    await state.set_state(EditNutritionState.waiting_for_year)


async def edit_month_callback(callback: types.CallbackQuery, state: FSMContext):
    language_code = get_user_language(str(callback.from_user.id))
    _, _, ym = callback.data.split(":", maxsplit=2)
    year, month = ym.split("-")
    await state.update_data(selected_year=year, selected_month=month)
    user_data = load_user_data_new(str(callback.from_user.id))
    days = sorted({date.split("-")[2] for date in user_data if date.startswith(f"{year}-{month}")})
    buttons = [
        [InlineKeyboardButton(text=day, callback_data=f"edit:date:{year}-{month}-{day}")]
        for day in days
    ]
    buttons.append(_back_button(language_code, "edit:back_to_month"))
    await callback.message.edit_text(
        t(language_code, "telegram.edit.choose_day", year=year, month=month),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(EditNutritionState.waiting_for_day)


async def edit_back_to_month(callback: types.CallbackQuery, state: FSMContext):
    language_code = get_user_language(str(callback.from_user.id))
    await callback.answer()
    data = await state.get_data()
    year = data.get("selected_year")
    user_data = load_user_data_new(str(callback.from_user.id))
    months = sorted({date.split("-")[1] for date in user_data if date.startswith(year)})
    buttons = [
        [InlineKeyboardButton(text=month, callback_data=f"edit:month:{year}-{month}")]
        for month in months
    ]
    buttons.append(_back_button(language_code, "edit:back_to_year"))
    await callback.message.edit_text(
        t(language_code, "telegram.edit.choose_month", year=year),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(EditNutritionState.waiting_for_month)


async def edit_date_callback(callback: types.CallbackQuery, state: FSMContext):
    language_code = get_user_language(str(callback.from_user.id))
    _, _, selected_date = callback.data.split(":", maxsplit=2)
    await state.update_data(selected_date=selected_date)
    user_data = load_user_data_new(str(callback.from_user.id))
    meals = user_data.get(selected_date, {})
    buttons = [
        [InlineKeyboardButton(text=f"{t(language_code, 'stats.meal')} {meal}", callback_data=f"edit:meal:{meal}")]
        for meal in sorted(meals.keys(), key=int)
    ]
    buttons.append(_back_button(language_code, "edit:back_to_date"))
    await callback.message.edit_text(
        t(language_code, "telegram.edit.choose_meal", date=selected_date),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(EditNutritionState.waiting_for_meal)


async def edit_back_to_date(callback: types.CallbackQuery, state: FSMContext):
    language_code = get_user_language(str(callback.from_user.id))
    await callback.answer()
    data = await state.get_data()
    year = data.get("selected_year")
    month = data.get("selected_month")
    user_data = load_user_data_new(str(callback.from_user.id))
    days = sorted({date.split("-")[2] for date in user_data if date.startswith(f"{year}-{month}")})
    buttons = [
        [InlineKeyboardButton(text=day, callback_data=f"edit:date:{year}-{month}-{day}")]
        for day in days
    ]
    buttons.append(_back_button(language_code, "edit:back_to_month"))
    await callback.message.edit_text(
        t(language_code, "telegram.edit.choose_day", year=year, month=month),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(EditNutritionState.waiting_for_day)


async def edit_meal_callback(callback: types.CallbackQuery, state: FSMContext):
    language_code = get_user_language(str(callback.from_user.id))
    _, _, meal_no = callback.data.split(":", maxsplit=2)
    await state.update_data(selected_meal=meal_no)
    data = await state.get_data()
    user_data = load_user_data_new(str(callback.from_user.id))
    items = user_data[data["selected_date"]][meal_no]
    buttons = [
        [InlineKeyboardButton(text=f"{idx + 1}. {item['name']}", callback_data=f"edit:item:{idx}")]
        for idx, item in enumerate(items)
    ]
    buttons.append([InlineKeyboardButton(text=t(language_code, "telegram.edit.delete_meal"), callback_data="edit:delete_meal")])
    buttons.append(_back_button(language_code, "edit:back_to_meals"))
    await callback.message.edit_text(
        t(language_code, "telegram.edit.choose_item", meal=meal_no),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(EditNutritionState.waiting_for_item)


async def edit_back_to_meals(callback: types.CallbackQuery, state: FSMContext):
    language_code = get_user_language(str(callback.from_user.id))
    await callback.answer()
    data = await state.get_data()
    selected_date = data["selected_date"]
    user_data = load_user_data_new(str(callback.from_user.id))
    meals = user_data.get(selected_date, {})
    buttons = [
        [InlineKeyboardButton(text=f"{t(language_code, 'stats.meal')} {meal}", callback_data=f"edit:meal:{meal}")]
        for meal in sorted(meals.keys(), key=int)
    ]
    buttons.append(_back_button(language_code, "edit:back_to_date"))
    await callback.message.edit_text(
        t(language_code, "telegram.edit.choose_meal", date=selected_date),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(EditNutritionState.waiting_for_meal)


async def edit_item_callback(callback: types.CallbackQuery, state: FSMContext):
    language_code = get_user_language(str(callback.from_user.id))
    _, _, idx = callback.data.split(":", maxsplit=2)
    await state.update_data(selected_item=int(idx))
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(language_code, "telegram.edit.delete_item"), callback_data="edit:action:delete_item")],
            _back_button(language_code, "edit:back_to_items"),
        ]
    )
    await callback.message.edit_text(
        t(language_code, "telegram.edit.choose_action"),
        reply_markup=keyboard,
    )
    await state.set_state(EditNutritionState.waiting_for_action)


async def edit_back_to_items(callback: types.CallbackQuery, state: FSMContext):
    language_code = get_user_language(str(callback.from_user.id))
    await callback.answer()
    data = await state.get_data()
    selected_date = data["selected_date"]
    meal_no = data["selected_meal"]
    user_data = load_user_data_new(str(callback.from_user.id))
    items = user_data[selected_date][meal_no]
    buttons = [
        [InlineKeyboardButton(text=f"{idx + 1}. {item['name']}", callback_data=f"edit:item:{idx}")]
        for idx, item in enumerate(items)
    ]
    buttons.append([InlineKeyboardButton(text=t(language_code, "telegram.edit.delete_meal"), callback_data="edit:delete_meal")])
    buttons.append(_back_button(language_code, "edit:back_to_meals"))
    await callback.message.edit_text(
        t(language_code, "telegram.edit.choose_item", meal=meal_no),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(EditNutritionState.waiting_for_item)


async def edit_delete_meal(callback: types.CallbackQuery, state: FSMContext):
    language_code = get_user_language(str(callback.from_user.id))
    await callback.answer()
    data = await state.get_data()
    user_id = str(callback.from_user.id)
    user_data = load_user_data_new(user_id)
    date_key = data["selected_date"]
    meal_no = data["selected_meal"]
    user_data[date_key].pop(meal_no, None)
    save_user_data_new(user_id, user_data)
    meals = user_data.get(date_key, {})
    if not meals:
        await callback.message.edit_text(t(language_code, "telegram.edit.no_meals_date"))
        await state.set_state(EditNutritionState.waiting_for_day)
        return await edit_back_to_date(callback, state)
    buttons = [
        [InlineKeyboardButton(text=f"{t(language_code, 'stats.meal')} {meal}", callback_data=f"edit:meal:{meal}")]
        for meal in sorted(meals.keys(), key=int)
    ]
    buttons.append(_back_button(language_code, "edit:back_to_date"))
    await callback.message.edit_text(
        t(language_code, "telegram.edit.meal_deleted", date=date_key, meal=meal_no),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(EditNutritionState.waiting_for_meal)


async def edit_delete_item(callback: types.CallbackQuery, state: FSMContext):
    language_code = get_user_language(str(callback.from_user.id))
    await callback.answer()
    data = await state.get_data()
    user_id = str(callback.from_user.id)
    user_data = load_user_data_new(user_id)
    date_key = data["selected_date"]
    meal_no = data["selected_meal"]
    idx = data["selected_item"]
    items = user_data[date_key][meal_no]
    items.pop(idx)
    save_user_data_new(user_id, user_data)
    if not items:
        await callback.message.edit_text(t(language_code, "telegram.edit.no_meals_date"))
        await state.clear()
        return await edit_back_to_date(callback, state)
    buttons = [
        [InlineKeyboardButton(text=f"{i + 1}. {item['name']}", callback_data=f"edit:item:{i}")]
        for i, item in enumerate(items)
    ]
    buttons.append([InlineKeyboardButton(text=t(language_code, "telegram.edit.delete_meal"), callback_data="edit:delete_meal")])
    buttons.append(_back_button(language_code, "edit:back_to_meals"))
    await callback.message.edit_text(
        t(language_code, "telegram.edit.item_deleted", item=idx + 1, meal=meal_no, date=date_key),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(EditNutritionState.waiting_for_item)
