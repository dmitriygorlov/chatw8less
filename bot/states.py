from aiogram.fsm.state import StatesGroup, State


class SaveNutritionData(StatesGroup):
    waiting_for_confirmation = State()


class LimitDataState(StatesGroup):
    waiting_for_action = State()
    waiting_for_limit_value = State()


class EditNutritionState(StatesGroup):
    waiting_for_year = State()  # Waiting for the user to select a year
    waiting_for_month = State()  # Waiting for the user to select a month
    waiting_for_day = State()  # Waiting for the user to select a day
    waiting_for_meal = State()  # Selecting the meal number for the date
    waiting_for_item = State()  # Selecting an item within the meal
    waiting_for_action = State()  # Choose to delete or back to meals


class HundredDataState(StatesGroup):
    waiting_for_description = State()


class OnlineSearchState(StatesGroup):
    waiting_for_query = State()


class SiteAccessState(StatesGroup):
    waiting_for_phrase = State()


class LanguageState(StatesGroup):
    waiting_for_language = State()
