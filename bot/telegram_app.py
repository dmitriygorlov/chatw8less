from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import BotCommand

from bot import callback_handlers, handlers
from bot.config import (
    ALLOWED_USER_IDS,
    DEFAULT_GPT_MODEL,
    DEFAULT_MODEL_MODE,
    HOUR_SHIFT,
    TELEGRAM_API_TOKEN,
)
from bot.logger_setup import setup_logging
from bot.middlewares import AllowedUserFilter, LoggingMiddleware
from bot.states import (
    EditNutritionState,
    HundredDataState,
    LimitDataState,
    LanguageState,
    OnlineSearchState,
    SiteAccessState,
    SaveNutritionData,
)


async def create_bot() -> Bot:
    if not TELEGRAM_API_TOKEN:
        raise RuntimeError("TELEGRAM_API_TOKEN is not configured")

    bot = Bot(token=TELEGRAM_API_TOKEN)
    commands = [
        BotCommand(command="help", description="Help"),
        BotCommand(command="stats", description="Nutrition statistics"),
        BotCommand(command="100", description="Nutrition per 100 g"),
        BotCommand(command="online", description="Online search"),
        BotCommand(command="edit", description="Edit nutrition data"),
        BotCommand(command="limits", description="Calorie limit"),
        BotCommand(command="model", description="Model mode"),
        BotCommand(command="language", description="Language"),
        BotCommand(command="site", description="Website and passphrase"),
        BotCommand(command="id", description="Telegram ID"),
    ]
    await bot.set_my_commands(commands)
    return bot


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.message.middleware(LoggingMiddleware())

    dp.message.register(handlers.cmd_start, Command(commands=["start"]))
    dp.message.register(
        handlers.cmd_help,
        Command(commands=["help"]),
        AllowedUserFilter(ALLOWED_USER_IDS),
    )
    dp.message.register(handlers.send_id, Command(commands=["id"]))
    dp.message.register(
        handlers.language_command,
        Command(commands=["language"]),
        AllowedUserFilter(ALLOWED_USER_IDS),
    )
    dp.callback_query.register(
        handlers.language_set_callback,
        F.data.startswith("language:set:"),
    )
    dp.callback_query.register(
        handlers.language_prompt_callback,
        F.data == "language:prompt",
    )
    dp.message.register(
        handlers.language_text_handler,
        LanguageState.waiting_for_language,
        AllowedUserFilter(ALLOWED_USER_IDS),
    )
    dp.message.register(
        handlers.site_command,
        Command(commands=["site"]),
        AllowedUserFilter(ALLOWED_USER_IDS),
    )
    dp.callback_query.register(
        handlers.site_phrase_prompt,
        F.data == "site:phrase:prompt",
    )
    dp.message.register(
        handlers.site_phrase_handler,
        SiteAccessState.waiting_for_phrase,
        AllowedUserFilter(ALLOWED_USER_IDS),
    )
    dp.message.register(
        handlers.cmd_stats,
        Command(commands=["stats"]),
        AllowedUserFilter(ALLOWED_USER_IDS),
    )
    dp.callback_query.register(
        callback_handlers.stats_callback,
        F.data.in_(["stats_today", "stats_yesterday", "stats_7days", "stats_all"]),
    )

    dp.message.register(
        handlers.limit_command,
        Command(commands=["limits"]),
        AllowedUserFilter(ALLOWED_USER_IDS),
    )
    dp.callback_query.register(
        callback_handlers.limit_menu_callback,
        LimitDataState.waiting_for_action,
        F.data.in_(["limit_set", "limit_view"]),
    )
    dp.message.register(
        handlers.limit_value_handler,
        LimitDataState.waiting_for_limit_value,
        AllowedUserFilter(ALLOWED_USER_IDS),
    )

    dp.message.register(
        handlers.edit_command,
        Command(commands=["edit"]),
        AllowedUserFilter(ALLOWED_USER_IDS),
    )
    dp.message.register(
        handlers.hundred_command,
        Command(commands=["100"]),
        AllowedUserFilter(ALLOWED_USER_IDS),
    )
    dp.message.register(
        handlers.hundred_description_handler,
        HundredDataState.waiting_for_description,
        AllowedUserFilter(ALLOWED_USER_IDS),
    )
    dp.message.register(
        handlers.online_command,
        Command(commands=["online"]),
        AllowedUserFilter(ALLOWED_USER_IDS),
    )
    dp.message.register(
        handlers.online_query_handler,
        OnlineSearchState.waiting_for_query,
        AllowedUserFilter(ALLOWED_USER_IDS),
    )

    dp.callback_query.register(
        callback_handlers.edit_year_callback,
        EditNutritionState.waiting_for_year,
        F.data.startswith("edit:year:"),
    )
    dp.callback_query.register(
        callback_handlers.edit_back_to_year,
        EditNutritionState.waiting_for_month,
        F.data == "edit:back_to_year",
    )
    dp.callback_query.register(
        callback_handlers.edit_month_callback,
        EditNutritionState.waiting_for_month,
        F.data.startswith("edit:month:"),
    )
    dp.callback_query.register(
        callback_handlers.edit_back_to_month,
        EditNutritionState.waiting_for_day,
        F.data == "edit:back_to_month",
    )
    dp.callback_query.register(
        callback_handlers.edit_date_callback,
        EditNutritionState.waiting_for_day,
        F.data.startswith("edit:date:"),
    )
    dp.callback_query.register(
        callback_handlers.edit_back_to_date,
        EditNutritionState.waiting_for_meal,
        F.data == "edit:back_to_date",
    )
    dp.callback_query.register(
        callback_handlers.edit_meal_callback,
        EditNutritionState.waiting_for_meal,
        F.data.startswith("edit:meal:"),
    )
    dp.callback_query.register(
        callback_handlers.edit_back_to_meals,
        EditNutritionState.waiting_for_item,
        F.data == "edit:back_to_meals",
    )
    dp.callback_query.register(
        callback_handlers.edit_item_callback,
        EditNutritionState.waiting_for_item,
        F.data.startswith("edit:item:"),
    )
    dp.callback_query.register(
        callback_handlers.edit_delete_meal,
        EditNutritionState.waiting_for_item,
        F.data == "edit:delete_meal",
    )
    dp.callback_query.register(
        callback_handlers.edit_back_to_items,
        EditNutritionState.waiting_for_action,
        F.data == "edit:back_to_items",
    )
    dp.callback_query.register(
        callback_handlers.edit_delete_item,
        EditNutritionState.waiting_for_action,
        F.data == "edit:action:delete_item",
    )
    dp.message.register(
        handlers.model_command,
        Command(commands=["model"]),
        AllowedUserFilter(ALLOWED_USER_IDS),
    )
    dp.message.register(
        handlers.handle_simple_message,
        AllowedUserFilter(ALLOWED_USER_IDS),
    )
    dp.callback_query.register(
        callback_handlers.save_confirmation,
        SaveNutritionData.waiting_for_confirmation,
        F.data.in_(["save_yes", "save_no"]),
    )
    dp.callback_query.register(
        callback_handlers.model_set_callback,
        F.data.startswith("model:set:"),
    )
    dp.callback_query.register(
        callback_handlers.vision_evaluate_callback,
        F.data == "vision:evaluate",
    )

    return dp


async def run_telegram_polling():
    setup_logging()
    print(f"Default model mode: {DEFAULT_MODEL_MODE} (model: {DEFAULT_GPT_MODEL})")
    print(f"Nutrition day shift: {HOUR_SHIFT:+d} hours")
    print(f"Allowed user IDs: {ALLOWED_USER_IDS}")
    bot = await create_bot()
    dp = build_dispatcher()
    await dp.start_polling(bot)
