import json
import logging

from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import (
    MODEL_MODES,
    SITE_URL,
    SUPPORT_CONTACT,
    TOKENS_LOG_FILE,
    TELEGRAM_API_TOKEN,
    SYSTEM_PROMPT_100,
)
from bot.chat_service import log_exchange, process_text_interaction
from bot.db import (
    get_user_assistant_name,
    get_user_language,
    set_user_language,
    set_user_passphrase,
    user_has_passphrase,
)
from bot.i18n import language_name, language_options, t
from bot.openai_client import (
    get_openai_response,
    get_openai_vision_response,
    get_openai_online_response,
)
from bot.states import (
    EditNutritionState,
    LanguageState,
    LimitDataState,
    SiteAccessState,
    SaveNutritionData,
    HundredDataState,
)
from bot.utils import (
    META_KEYS,
    get_response_finish_reason,
    get_user_model_config,
    load_user_data_new,
    log_token_usage,
    save_user_data_new,
    send_long_message,
)


# /start command
async def cmd_start(message: types.Message):
    return await send_long_message(
        message,
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "Я — бот-помощник, который помогает тебе быстро и легко рассчитывать калорийность и БЖУ твоих приемов пищи. 🍽️\n\n"
        f"Для начала давай просто познакомимся! Для этого нажми /id и отправь этот id @{SUPPORT_CONTACT}.\n\n"
        "🍏 *Как бот в целом работает?*\n"
        "Просто отправь мне список продуктов с весом, а я рассчитаю калории и дам полезный (или нет) совет.\n\n"
        "Например, напиши:\n"
        "`20 хлеба, 30 сыра, 10 масла`\n\n"
        "Если не знаешь - можешь отправить фото еды, и я попробую распознать продукты. 📸\n\n"
    "После распознавания жми кнопку *«⚖️ Оценить калории?»*, чтобы превратить список в полноценный расчёт.\n\n"
        "📝 Чтобы увидеть всю статистику, используй команду /stats.\n"
        "💡 Чтобы установить лимит калорий, используй команду /limits.\n\n"
        "� Хочешь выбирать модель (быстрая или умная)? Используй /model.\n\n"
        "�📏 Для расчёта БЖУ на 100 г любого блюда используй /100.\n\n"
        "❓ Если нужна помощь, нажми /help.",
        parse_mode="Markdown",
    )


# /help command
async def cmd_help(message: types.Message):
    return await send_long_message(
        message,
        "ℹ️ *Как пользоваться ботом?*\n\n"
        "🍎 *Расчёт калорий и БЖУ:*\n"
        "- Просто напиши список продуктов и их вес в граммах.\n"
        "- Например: `50 г курицы, 20 г риса, 10 г масла`\n\n"
        "📸 *Распознавание продуктов по фото:*\n"
        "- Отправь фото еды, и я попробую распознать продукты.\n"
        "- Лучше всего работает с чёткими изображениями без лишних объектов.\n\n"
    "- После ответа нажми кнопку *«⚖️ Оценить калории?»*, чтобы сохранить распознанное как приём пищи.\n\n"
        "💾 *Сохранение данных:*\n"
        "- После расчёта я предложу сохранить данные. Просто нажми «✅ Сохранить».\n\n"
        "📊 *Просмотр статистики:*\n"
        "- Введи команду `/stats` и выбери период (сегодня, вчера или вся статистика).\n\n"
        "🔥 *Лимит калорий:*\n"
        "- Введи команду `/limits`, чтобы установить или изменить дневной лимит калорий.\n\n"
        "✏️ *Редактирование данных:*\n"
        "- Введи команду `/edit`, чтобы удалить уже сохранённые приёмы пищи.\n\n"
        "🍽️ *Быстрый расчёт на 100 г:*\n"
        "- Нажми `/100` и отправь описание блюда, чтобы получить БЖУ на 100 г.\n\n"
        "🌐 *Поиск в интернете:*\n"
        "- Нажми `/online` для поиска информации о конкретных брендах, продуктах или фитнес-вопросах.\n"
        "- Например: `калорийность McDonald's Big Mac` или `лучшие упражнения для пресса`.\n\n"
        "🔀 *Выбор модели:*\n"
        "- Команда `/model` переключает режимы: ⚡ Быстрая и 🧠 Умная.\n"
        "- Выбор влияет на текстовые и фотоответы.\n\n"
        "🌍 *Сайт:*\n"
        "- Команда `/site` покажет ссылку на сайт и позволит задать или сменить свою кодовую фразу.\n\n"
        "🤔 *Остались вопросы?*\n"
        f"- Свяжись с @{SUPPORT_CONTACT}, если что-то не так.\n\n",
        parse_mode="Markdown",
    )


# /id command
async def send_id(message: types.Message):
    return await message.reply(f"Ваш Telegram ID: {message.from_user.id}")


def _build_site_keyboard(has_passphrase: bool) -> InlineKeyboardMarkup:
    action_text = "✏️ Сменить кодовую фразу" if has_passphrase else "🔐 Задать кодовую фразу"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=action_text, callback_data="site:phrase:prompt")]
        ]
    )


async def site_command(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    has_passphrase = user_has_passphrase(user_id)
    site_status = (
        f"Сайт: {SITE_URL}\n"
        if SITE_URL
        else "Сайт пока ещё не настроен по адресу. Фразу можно задать уже сейчас.\n"
    )
    phrase_status = (
        "Кодовая фраза уже настроена.\n"
        "Если хочешь, можешь сменить её кнопкой ниже."
        if has_passphrase
        else "Кодовая фраза ещё не настроена.\n"
        "Нажми кнопку ниже и отправь свою фразу одним сообщением."
    )

    return await send_long_message(
        message,
        f"{site_status}\n"
        f"{phrase_status}\n\n"
        "После входа сайт запомнит сессию, и ты сможешь продолжать ту же историю, что и в Telegram.",
        method="reply",
        reply_markup=_build_site_keyboard(has_passphrase),
        disable_web_page_preview=True,
    )


async def site_phrase_prompt(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SiteAccessState.waiting_for_phrase)
    site_hint = f"Сайт: {SITE_URL}\n\n" if SITE_URL else ""
    return await send_long_message(
        callback.message,
        f"{site_hint}"
        "Отправь новую кодовую фразу одним сообщением.\n\n"
        "Лучше использовать фразу длиной хотя бы 8 символов.\n"
        "Для отмены отправь `отмена`.",
        method="reply",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


async def site_phrase_handler(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    phrase = (message.text or "").strip()

    if phrase.lower() in {"отмена", "/cancel", "cancel"}:
        await state.clear()
        return await send_long_message(
            message,
            "Настройка входа на сайт отменена.",
            method="reply",
        )

    if len(phrase) < 6:
        return await send_long_message(
            message,
            "Кодовая фраза слишком короткая. Используй хотя бы 6 символов или отправь `отмена`.",
            method="reply",
            parse_mode="Markdown",
        )

    set_user_passphrase(user_id, phrase)
    await state.clear()

    site_hint = (
        f"Открыть сайт:\n{SITE_URL}\n\n"
        if SITE_URL
        else "Ссылка на сайт пока не настроена. Когда `SITE_URL` появится, команда `/site` покажет адрес.\n\n"
    )
    return await send_long_message(
        message,
        f"Кодовая фраза сохранена.\n\n{site_hint}"
        "Теперь этой фразой можно входить в веб-версию.",
        method="reply",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


# /stats command
async def cmd_stats(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Сегодня", callback_data="stats_today")],
            [InlineKeyboardButton(text="🟡 Вчера", callback_data="stats_yesterday")],
            [InlineKeyboardButton(text="🗓️ 7 дней", callback_data="stats_7days")],
            [InlineKeyboardButton(text="📊 Всё время", callback_data="stats_all")],
        ]
    )
    return await send_long_message(
        message,
        "Выбери период, за который хочешь посмотреть статистику:",
        reply_markup=keyboard,
    )


# /limit command
async def limit_command(message: types.Message, state: FSMContext):
    """Показываем меню: установить лимит или посмотреть дефицит."""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Изменить лимит", callback_data="limit_set")],
            [InlineKeyboardButton(text="Показать дефицит", callback_data="limit_view")],
        ]
    )
    await message.reply(
        "Что вы хотите сделать с дневным лимитом?", reply_markup=keyboard
    )
    await state.set_state(LimitDataState.waiting_for_action)


async def limit_value_handler(message: types.Message, state: FSMContext):
    """Сохраняем введённый пользователем лимит."""
    user_id = str(message.from_user.id)
    text = message.text.strip()
    if not text.isdigit():
        return await send_long_message(
            message,
            "Пожалуйста, введите целое число без лишних символов.",
            method="reply",
        )
    limit = int(text)

    # Обновляем или создаём данные пользователя
    user_data = load_user_data_new(user_id)
    user_data["daily_limit"] = limit

    # сохраняем без изменения других полей
    save_user_data_new(user_id, user_data)

    await send_long_message(
        message, f"Дневной лимит установлен: {limit} ккал.", method="reply"
    )
    await state.clear()


async def edit_command(message: types.Message, state: FSMContext):
    """Display the menu for editing data."""
    user_id = str(message.from_user.id)
    user_data = load_user_data_new(user_id)

    dates = [d for d in user_data.keys() if d not in META_KEYS]
    if not dates:
        return await send_long_message(
            message,
            "Нет сохранённых приёмов пищи для редактирования.",
            method="reply",
        )

    years = sorted({d.split("-")[0] for d in dates})
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=y, callback_data=f"edit:year:{y}")]
            for y in years
        ]
    )
    await send_long_message(
        message, "Выберите год для редактирования:", method="reply", reply_markup=kb
    )
    await state.set_state(EditNutritionState.waiting_for_year)


async def hundred_command(message: types.Message, state: FSMContext):
    """Prompt user to send description for per-100g calculation."""
    await send_long_message(
        message,
        "Отправьте список ингредиентов или описание блюда, и я посчитаю калорийность на 100 г.",
        method="reply",
    )
    await state.set_state(HundredDataState.waiting_for_description)


async def hundred_description_handler(message: types.Message, state: FSMContext):
    """Handle user text and calculate per 100 g values."""
    user_id = str(message.from_user.id)
    mode, model_config = get_user_model_config(user_id)
    try:
        response = await get_openai_response(
            message.text,
            system_prompt=SYSTEM_PROMPT_100,
            model=model_config["model"],
            max_tokens=model_config["max_tokens"],
        )
        answer = response.output_text
        usage = response.usage
        finish_reason = get_response_finish_reason(response)

        log_token_usage(
            usage,
            message.from_user.id,
            finish_reason,
            tokens_log_file=TOKENS_LOG_FILE,
            function_name=f"per_100g[{mode}]",
            model=model_config["model"],
        )
        log_exchange(str(message.from_user.id), message.text, answer, source="telegram")
        await send_long_message(message, answer, method="reply")
    except Exception as e:
        logging.error(
            f"Error processing per 100 g request for user {message.from_user.id}: {e}"
        )
        await send_long_message(
            message,
            "Произошла ошибка при обработке запроса. Попробуйте ещё раз позже.",
            method="reply",
        )
    finally:
        await state.clear()


async def online_command(message: types.Message, state: FSMContext):
    """Промпт пользователя отправить запрос для поиска в интернете."""
    from bot.states import OnlineSearchState

    await send_long_message(
        message,
        "🌐 Отправь свой вопрос или запрос для поиска в интернете.\n\n"
        "Примеры:\n"
        "- Калорийность Биг Мака из McDonald's\n"
        "- Состав БЖУ Snickers 50 г\n"
        "- Лучшие упражнения для похудения\n"
        "- Как правильно рассчитать дефицит калорий",
        method="reply",
    )
    await state.set_state(OnlineSearchState.waiting_for_query)


async def online_query_handler(message: types.Message, state: FSMContext):
    """Обработка запроса для онлайн-поиска."""
    user_id = str(message.from_user.id)
    mode, model_config = get_user_model_config(user_id)

    try:
        response = await get_openai_online_response(
            message.text,
            model=model_config["model"],
            max_tokens=model_config["max_tokens"],
        )
        answer = response.output_text
        usage = response.usage
        finish_reason = get_response_finish_reason(response)

        log_token_usage(
            usage,
            message.from_user.id,
            finish_reason,
            tokens_log_file=TOKENS_LOG_FILE,
            function_name=f"online_search[{mode}]",
            model=model_config["model"],
        )
        log_exchange(str(message.from_user.id), message.text, answer, source="telegram")
        await send_long_message(message, answer, method="reply")
    except Exception as e:
        logging.error(
            f"Error processing online search for user {message.from_user.id}: {e}"
        )
        await send_long_message(
            message,
            "Произошла ошибка при обработке запроса. Попробуйте ещё раз позже.",
            method="reply",
        )
    finally:
        await state.clear()


# Processing any message that is not a command
async def handle_simple_message(message: types.Message, state: FSMContext):
    """
    Обрабатываем текстовые сообщения, которые не являются командами.
    Если в сообщении есть фото, используем OpenAI Vision API для распознавания продуктов.
    """
    try:
        user_id = str(message.from_user.id)
        mode, model_config = get_user_model_config(user_id)

        # If there is photo in the message, we will use OpenAI Vision API
        if message.photo:
            # for better quality, we take the largest photo
            # (the last one in the list is the largest)
            largest_photo = message.photo[-1]
            file_id = largest_photo.file_id

            # Get the file path from Telegram
            file = await message.bot.get_file(file_id)
            file_path = file.file_path
            photo_url = (
                f"https://api.telegram.org/file/bot{TELEGRAM_API_TOKEN}/{file_path}"
            )

            # Additionally, we can get the caption if it exists
            caption = message.caption or ""

            vision_response = await get_openai_vision_response(
                image_url=photo_url,
                user_comment=caption,
                model=model_config.get("vision_model"),
                max_tokens=model_config.get("max_tokens"),
            )

            # Обычно текст в vision_response.output_text
            answer = vision_response.output_text
            # Логирование токенов (оставляем как было)
            usage = vision_response.usage
            finish_reason = get_response_finish_reason(vision_response)

            log_token_usage(
                usage,
                message.from_user.id,
                finish_reason,
                tokens_log_file=TOKENS_LOG_FILE,
                function_name=f"vision_food_recognition[{mode}]",
                model=model_config.get("vision_model"),
            )

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⚖️ Оценить калории?",
                            callback_data="vision:evaluate",
                        )
                    ]
                ]
            )

            await send_long_message(
                message,
                answer,
                method="reply",
                reply_markup=keyboard,
            )  # Просто возвращаем текстовое описание

            await state.update_data(
                vision_candidate={"text": answer, "caption": caption}
            )

            return

        # Вызываем OpenAI с указанием функции, чтобы получить ответ с JSON-структурой
        interaction = await process_text_interaction(
            user_id=user_id,
            user_text=message.text,
            source="telegram",
            auto_save_meal=False,
        )
        final_answer = interaction["reply_text"]
        # сохраняем текущие данные в state временно для подтверждения
        await state.update_data(nutrition_data={"items": interaction["items"]})

        # отправляем сообщение с inline-кнопками
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Сохранить", callback_data="save_yes"),
                    InlineKeyboardButton(text="❌ Не надо", callback_data="save_no"),
                ]
            ]
        )
        # отправляем сообщение с ответом и кнопками
        sent: types.Message = await send_long_message(
            message, final_answer, method="reply", reply_markup=keyboard
        )

        # переходим в состояние ожидания подтверждения
        await state.set_state(SaveNutritionData.waiting_for_confirmation)

        # возвращаем сообщение для логирования
        return sent

    except Exception as e:
        logging.error(
            f"Error processing OpenAI API for user {message.from_user.id}: {e}"
        )
        err_msg = await send_long_message(
            message,
            f"Произошла ошибка при обработке запроса, попробуйте пожаловаться @{SUPPORT_CONTACT}.",
            method="reply",
        )
        return err_msg


async def model_command(message: types.Message):
    user_id = str(message.from_user.id)
    current_mode, model_config = get_user_model_config(user_id)

    text = (
        "Выберите режим модели для ответов.\n"
        f"Сейчас установлено: {model_config['label']} ({model_config['model']}).\n"
        "Настройка применяется и к текстовым, и к фотоответам."
    )

    buttons = []
    for mode_key, info in MODEL_MODES.items():
        prefix = "✅ " if mode_key == current_mode else ""
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix}{info['label']}",
                    callback_data=f"model:set:{mode_key}",
                )
            ]
        )

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    return await send_long_message(
        message,
        text,
        method="reply",
        reply_markup=keyboard,
    )


# ---------------------------- Localized command overrides ---------------------------- #

def _message_language(message: types.Message) -> str:
    return get_user_language(str(message.from_user.id))


def _callback_language(callback: types.CallbackQuery) -> str:
    return get_user_language(str(callback.from_user.id))


def _localized_model_label(language_code: str, mode_key: str) -> str:
    return t(language_code, f"mode.{mode_key}")


def _language_keyboard(current: str) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{'✅ ' if option['code'] == current else ''}{option['name']}",
                callback_data=f"language:set:{option['code']}",
            )
        ]
        for option in language_options()
    ]
    buttons.append([InlineKeyboardButton(text=t(current, "language.other"), callback_data="language:prompt")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def language_command(message: types.Message):
    language_code = _message_language(message)
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        return await _generate_language_from_text(message, parts[1].strip(), language_code)
    return await send_long_message(
        message,
        t(language_code, "language.command"),
        method="reply",
        reply_markup=_language_keyboard(language_code),
    )


async def _generate_language_from_text(message: types.Message, requested: str, current_language: str):
    from bot.app_services import generate_and_set_language

    waiting = await send_long_message(
        message,
        t(current_language, "language.generating"),
        method="reply",
    )
    try:
        settings = await generate_and_set_language(str(message.from_user.id), requested)
    except Exception as exc:
        logging.error("Could not generate language %s: %s", requested, exc)
        return await send_long_message(
            message,
            t(current_language, "language.invalid"),
            method="reply",
        )
    new_language = settings["language_code"]
    return await send_long_message(
        message,
        t(
            new_language,
            "language.generated",
            language_name=language_name(new_language),
        ),
        method="reply",
        reply_markup=_language_keyboard(new_language),
    )


async def language_prompt_callback(callback: types.CallbackQuery, state: FSMContext):
    language_code = _callback_language(callback)
    await callback.answer()
    await state.set_state(LanguageState.waiting_for_language)
    await callback.message.answer(t(language_code, "language.prompt_custom"))


async def language_text_handler(message: types.Message, state: FSMContext):
    current_language = _message_language(message)
    text = (message.text or "").strip()
    if text.lower() in {"отмена", "cancel", "otkaži", "/cancel"}:
        await state.clear()
        return await send_long_message(message, t(current_language, "telegram.site.cancelled"), method="reply")
    await state.clear()
    return await _generate_language_from_text(message, text, current_language)


async def language_set_callback(callback: types.CallbackQuery):
    try:
        _, _, language_code = callback.data.split(":", maxsplit=2)
        normalized = set_user_language(str(callback.from_user.id), language_code)
    except ValueError:
        language_code = _callback_language(callback)
        await callback.answer(t(language_code, "language.invalid"))
        return

    await callback.answer(t(normalized, "language.saved_toast"))
    await callback.message.edit_text(
        t(
            normalized,
            "language.changed",
            language_name=language_name(normalized),
        ),
        reply_markup=_language_keyboard(normalized),
    )


async def cmd_start(message: types.Message):
    language_code = _message_language(message)
    return await send_long_message(
        message,
        t(
            language_code,
            "telegram.start",
            first_name=message.from_user.first_name or "",
            assistant_name=get_user_assistant_name(str(message.from_user.id)),
            support_contact=SUPPORT_CONTACT,
        ),
        parse_mode="Markdown",
    )


async def cmd_help(message: types.Message):
    language_code = _message_language(message)
    return await send_long_message(
        message,
        t(language_code, "telegram.help", support_contact=SUPPORT_CONTACT),
        parse_mode="Markdown",
    )


async def send_id(message: types.Message):
    language_code = _message_language(message)
    return await message.reply(t(language_code, "telegram.id", user_id=message.from_user.id))


def _build_site_keyboard(has_passphrase: bool, language_code: str | None = None) -> InlineKeyboardMarkup:
    action_text = (
        t(language_code, "telegram.site.button_change")
        if has_passphrase
        else t(language_code, "telegram.site.button_set")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=action_text, callback_data="site:phrase:prompt")]
        ]
    )


async def site_command(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    language_code = _message_language(message)
    await state.clear()
    has_passphrase = user_has_passphrase(user_id)
    site_status = (
        t(language_code, "telegram.site.command_configured", site_url=SITE_URL)
        if SITE_URL
        else t(language_code, "telegram.site.command_missing")
    )
    phrase_status = (
        t(language_code, "telegram.site.passphrase_ready")
        if has_passphrase
        else t(language_code, "telegram.site.passphrase_missing")
    )
    assistant_status = t(
        language_code,
        "telegram.site.assistant_status",
        assistant_name=get_user_assistant_name(user_id),
    )
    return await send_long_message(
        message,
        t(
            language_code,
            "telegram.site.intro",
            site_status=site_status,
            phrase_status=phrase_status,
            assistant_status=assistant_status,
        ),
        method="reply",
        reply_markup=_build_site_keyboard(has_passphrase, language_code),
        disable_web_page_preview=True,
    )


async def site_phrase_prompt(callback: types.CallbackQuery, state: FSMContext):
    language_code = _callback_language(callback)
    await callback.answer()
    await state.set_state(SiteAccessState.waiting_for_phrase)
    site_hint = f"{t(language_code, 'common.site')}: {SITE_URL}\n\n" if SITE_URL else ""
    return await send_long_message(
        callback.message,
        t(language_code, "telegram.site.phrase_prompt", site_hint=site_hint),
        method="reply",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


async def site_phrase_handler(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    language_code = _message_language(message)
    phrase = (message.text or "").strip()
    cancel_words = {"отмена", "/cancel", "cancel", "otkaži", "откажи"}

    if phrase.lower() in cancel_words:
        await state.clear()
        return await send_long_message(
            message,
            t(language_code, "telegram.site.cancelled"),
            method="reply",
        )

    if len(phrase) < 6:
        return await send_long_message(
            message,
            t(language_code, "telegram.site.short_phrase"),
            method="reply",
            parse_mode="Markdown",
        )

    set_user_passphrase(user_id, phrase)
    await state.clear()
    site_hint = (
        t(language_code, "telegram.site.open", site_url=SITE_URL)
        if SITE_URL
        else t(language_code, "telegram.site.unconfigured")
    )
    return await send_long_message(
        message,
        t(language_code, "telegram.site.saved", site_hint=site_hint),
        method="reply",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


async def cmd_stats(message: types.Message):
    language_code = _message_language(message)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🟢 {t(language_code, 'stats.today')}", callback_data="stats_today")],
            [InlineKeyboardButton(text=f"🟡 {t(language_code, 'stats.yesterday')}", callback_data="stats_yesterday")],
            [InlineKeyboardButton(text=f"🗓️ {t(language_code, 'web.stats_7')}", callback_data="stats_7days")],
            [InlineKeyboardButton(text=f"📊 {t(language_code, 'web.all')}", callback_data="stats_all")],
        ]
    )
    return await send_long_message(
        message,
        t(language_code, "telegram.stats.choose"),
        reply_markup=keyboard,
    )


async def limit_command(message: types.Message, state: FSMContext):
    language_code = _message_language(message)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(language_code, "telegram.limit.set"), callback_data="limit_set")],
            [InlineKeyboardButton(text=t(language_code, "telegram.limit.view"), callback_data="limit_view")],
        ]
    )
    await message.reply(t(language_code, "telegram.limit.menu"), reply_markup=keyboard)
    await state.set_state(LimitDataState.waiting_for_action)


async def limit_value_handler(message: types.Message, state: FSMContext):
    language_code = _message_language(message)
    user_id = str(message.from_user.id)
    text = (message.text or "").strip()
    if not text.isdigit():
        return await send_long_message(message, t(language_code, "telegram.limit.invalid"), method="reply")
    limit = int(text)
    user_data = load_user_data_new(user_id)
    user_data["daily_limit"] = limit
    save_user_data_new(user_id, user_data)
    await send_long_message(message, t(language_code, "telegram.limit.saved", limit=limit), method="reply")
    await state.clear()


async def edit_command(message: types.Message, state: FSMContext):
    language_code = _message_language(message)
    user_id = str(message.from_user.id)
    user_data = load_user_data_new(user_id)
    dates = [date for date in user_data.keys() if date not in META_KEYS]
    if not dates:
        return await send_long_message(
            message,
            t(language_code, "telegram.edit.no_meals"),
            method="reply",
        )

    years = sorted({date.split("-")[0] for date in dates})
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=year, callback_data=f"edit:year:{year}")]
            for year in years
        ]
    )
    await send_long_message(
        message,
        t(language_code, "telegram.edit.choose_year"),
        method="reply",
        reply_markup=keyboard,
    )
    await state.set_state(EditNutritionState.waiting_for_year)


async def hundred_command(message: types.Message, state: FSMContext):
    language_code = _message_language(message)
    await send_long_message(message, t(language_code, "telegram.100.prompt"), method="reply")
    await state.set_state(HundredDataState.waiting_for_description)


async def online_command(message: types.Message, state: FSMContext):
    language_code = _message_language(message)
    from bot.states import OnlineSearchState

    await send_long_message(message, t(language_code, "telegram.online.prompt"), method="reply")
    await state.set_state(OnlineSearchState.waiting_for_query)


async def model_command(message: types.Message):
    user_id = str(message.from_user.id)
    language_code = _message_language(message)
    current_mode, model_config = get_user_model_config(user_id)
    label = _localized_model_label(language_code, current_mode)
    text = t(language_code, "telegram.model.command", label=label, model=model_config["model"])

    buttons = []
    for mode_key, info in MODEL_MODES.items():
        prefix = "✅ " if mode_key == current_mode else ""
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix}{_localized_model_label(language_code, mode_key)}",
                    callback_data=f"model:set:{mode_key}",
                )
            ]
        )

    return await send_long_message(
        message,
        text,
        method="reply",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


async def hundred_description_handler(message: types.Message, state: FSMContext):
    language_code = _message_language(message)
    user_id = str(message.from_user.id)
    try:
        from bot.app_services import analyze_hundred

        result = await analyze_hundred(user_id, message.text or "")
        log_exchange(user_id, message.text or "", result["text"], source="telegram")
        await send_long_message(message, result["text"], method="reply")
    except Exception as e:
        logging.error("Error processing per 100 g request for user %s: %s", user_id, e)
        await send_long_message(message, t(language_code, "telegram.error.generic"), method="reply")
    finally:
        await state.clear()


async def online_query_handler(message: types.Message, state: FSMContext):
    language_code = _message_language(message)
    user_id = str(message.from_user.id)
    try:
        from bot.app_services import analyze_online

        result = await analyze_online(user_id, message.text or "")
        log_exchange(user_id, message.text or "", result["text"], source="telegram")
        await send_long_message(message, result["text"], method="reply")
    except Exception as e:
        logging.error("Error processing online search for user %s: %s", user_id, e)
        await send_long_message(message, t(language_code, "telegram.error.generic"), method="reply")
    finally:
        await state.clear()


async def handle_simple_message(message: types.Message, state: FSMContext):
    language_code = _message_language(message)
    user_id = str(message.from_user.id)
    try:
        mode, model_config = get_user_model_config(user_id)

        if message.photo:
            largest_photo = message.photo[-1]
            file = await message.bot.get_file(largest_photo.file_id)
            photo_url = f"https://api.telegram.org/file/bot{TELEGRAM_API_TOKEN}/{file.file_path}"
            caption = message.caption or ""

            from bot.app_services import analyze_photo

            result = await analyze_photo(user_id, photo_url, caption=caption)
            answer = result["recognized_text"]
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(language_code, "telegram.photo.evaluate"),
                            callback_data="vision:evaluate",
                        )
                    ]
                ]
            )
            await send_long_message(message, answer, method="reply", reply_markup=keyboard)
            await state.update_data(vision_candidate={"text": answer, "caption": caption})
            return

        interaction = await process_text_interaction(
            user_id=user_id,
            user_text=message.text or "",
            source="telegram",
            auto_save_meal=False,
        )
        await state.update_data(nutrition_data={"items": interaction["items"]})
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text=t(language_code, "telegram.save.yes"), callback_data="save_yes"),
                    InlineKeyboardButton(text=t(language_code, "telegram.no"), callback_data="save_no"),
                ]
            ]
        )
        sent = await send_long_message(
            message,
            interaction["reply_text"],
            method="reply",
            reply_markup=keyboard,
        )
        await state.set_state(SaveNutritionData.waiting_for_confirmation)
        return sent

    except Exception as e:
        logging.error("Error processing OpenAI API for user %s: %s", user_id, e)
        return await send_long_message(
            message,
            t(language_code, "telegram.error.support", support_contact=SUPPORT_CONTACT),
            method="reply",
        )
