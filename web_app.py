import asyncio
import base64
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from bot.app_services import (
    analyze_hundred,
    analyze_photo,
    delete_item,
    delete_meal,
    get_history as get_nutrition_history,
    get_statistics_bundle,
    get_user_settings,
    generate_and_set_language,
    import_legacy_storage_zip,
    save_meal,
    set_assistant_name,
    set_language,
    set_daily_limit,
    set_model_mode,
    validate_limit,
)
from bot.chat_service import (
    get_chat_history,
    log_exchange,
    process_online_interaction,
    process_text_interaction,
)
from bot.config import (
    MODEL_MODES,
    TELEGRAM_API_TOKEN,
    WEB_SESSION_DAYS,
    should_run_telegram_in_web,
    should_use_secure_cookies,
)
from bot.db import (
    create_web_session,
    delete_web_session,
    get_user_by_passphrase,
    get_user_by_session_token,
    init_db,
)
from bot.i18n import locale_payload, parse_accept_language, t
from bot.logger_setup import setup_logging
from bot.telegram_app import run_telegram_polling


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
SESSION_COOKIE = "chatw8less_session"
ASSET_VERSION = str(
    int(
        max(
            (STATIC_DIR / "styles.css").stat().st_mtime,
            (STATIC_DIR / "app.js").stat().st_mtime,
        )
    )
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db()
    if TELEGRAM_API_TOKEN and should_run_telegram_in_web():
        app.state.telegram_task = asyncio.create_task(run_telegram_polling())
    else:
        app.state.telegram_task = None
    try:
        yield
    finally:
        task = getattr(app.state, "telegram_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="ChatW8Less", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.middleware("http")
async def disable_dynamic_response_caching(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


class SendMessageRequest(BaseModel):
    text: str
    save: bool = True


class AnalyzeTextRequest(BaseModel):
    text: str
    save: bool = False


class SaveMealRequest(BaseModel):
    items: list[dict[str, Any]]
    date: str | None = None


class LimitRequest(BaseModel):
    daily_limit: int | str | None


class ModeRequest(BaseModel):
    mode: str


class LanguageRequest(BaseModel):
    language_code: str
    generate: bool = False


class AssistantNameRequest(BaseModel):
    assistant_name: str


class TextActionRequest(BaseModel):
    text: str


def _get_session_token(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE)


def _get_current_user(request: Request) -> dict | None:
    token = _get_session_token(request)
    if not token:
        return None
    return get_user_by_session_token(token)


def _require_user(request: Request) -> dict:
    user = _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


def _session_cookie_kwargs(request: Request) -> dict:
    return {
        "httponly": True,
        "samesite": "lax",
        "max_age": 60 * 60 * 24 * WEB_SESSION_DAYS,
        "secure": should_use_secure_cookies(request.url.scheme),
    }


def _user_payload(user: dict) -> dict[str, Any]:
    return {
        "id": user["id"],
        "display_name": user.get("display_name") or user["id"],
        "telegram_user_id": user.get("telegram_user_id"),
        "language_code": user.get("language_code") or "en",
    }


def _mode_options(language_code: str) -> list[dict[str, str]]:
    return [
        {
            "key": key,
            "label": t(language_code, f"mode.{key}"),
            "model": info["model"],
        }
        for key, info in MODEL_MODES.items()
    ]


def _dashboard_payload(user: dict) -> dict[str, Any]:
    user_id = user["id"]
    language_code = user.get("language_code") or "en"
    return {
        "user": _user_payload(user),
        "messages": get_chat_history(user_id),
        "settings": get_user_settings(user_id),
        "stats": get_statistics_bundle(user_id),
        "nutrition_history": get_nutrition_history(user_id),
        "available_modes": _mode_options(language_code),
        "locale": locale_payload(language_code),
    }


def _require_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Text is required")
    return normalized


def _structured_food_error(exc: ValueError) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail="Не удалось получить структурированный расчёт. Попробуй ещё раз или переключи режим модели.",
    )


def _photo_to_data_url(photo: UploadFile, raw_bytes: bytes) -> str:
    content_type = (photo.content_type or "").strip().lower()
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image uploads are supported")
    encoded = base64.b64encode(raw_bytes).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = _get_current_user(request)
    language_code = (
        user.get("language_code")
        if user
        else parse_accept_language(request.headers.get("accept-language"))
    )
    locale = locale_payload(language_code)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "authenticated": bool(user),
            "current_user": user,
            "login_error": request.query_params.get("login_error") == "1",
            "language_code": locale["language_code"],
            "locale": locale,
            "tr": locale["messages"],
            "asset_version": ASSET_VERSION,
        },
    )


@app.post("/login")
async def login(request: Request, phrase: str = Form(...)):
    normalized_phrase = phrase.strip()
    if not normalized_phrase:
        return RedirectResponse(url="/?login_error=1", status_code=303)

    user = get_user_by_passphrase(normalized_phrase)
    if not user:
        return RedirectResponse(url="/?login_error=1", status_code=303)

    session_token = create_web_session(user["id"])
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(SESSION_COOKIE, session_token, **_session_cookie_kwargs(request))
    return response


@app.post("/logout")
async def logout(request: Request):
    token = _get_session_token(request)
    if token:
        delete_web_session(token)
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(
        SESSION_COOKIE,
        secure=should_use_secure_cookies(request.url.scheme),
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/favicon.ico")
async def favicon():
    return RedirectResponse(url="/static/favicon.png", status_code=307)


@app.get("/api/bootstrap")
async def api_bootstrap(request: Request):
    user = _require_user(request)
    return _dashboard_payload(user)


@app.get("/api/history")
async def api_history(request: Request):
    user = _require_user(request)
    return {
        "user": _user_payload(user),
        "messages": get_chat_history(user["id"]),
    }


@app.get("/api/nutrition-history")
async def api_nutrition_history(request: Request):
    user = _require_user(request)
    return {
        "history": get_nutrition_history(user["id"]),
        "stats": get_statistics_bundle(user["id"]),
    }


@app.post("/api/message")
async def api_message(request: Request, payload: SendMessageRequest):
    user = _require_user(request)
    text = _require_text(payload.text)

    try:
        result = await process_text_interaction(
            user_id=user["id"],
            user_text=text,
            source="web",
            auto_save_meal=payload.save,
        )
    except ValueError as exc:
        raise _structured_food_error(exc) from exc
    return {
        "reply_text": result["reply_text"],
        "messages": result["history"],
        "items": result["items"],
        "saved": result["saved"],
        "settings": get_user_settings(user["id"]),
        "stats": get_statistics_bundle(user["id"]),
        "nutrition_history": get_nutrition_history(user["id"]),
    }


@app.post("/api/analyze-text")
async def api_analyze_text(request: Request, payload: AnalyzeTextRequest):
    user = _require_user(request)
    text = _require_text(payload.text)

    try:
        result = await process_text_interaction(
            user_id=user["id"],
            user_text=text,
            source="web",
            auto_save_meal=payload.save,
        )
    except ValueError as exc:
        raise _structured_food_error(exc) from exc
    return {
        "reply_text": result["reply_text"],
        "messages": result["history"],
        "items": result["items"],
        "saved": result["saved"],
        "settings": get_user_settings(user["id"]),
        "stats": get_statistics_bundle(user["id"]),
        "nutrition_history": get_nutrition_history(user["id"]),
    }


@app.post("/api/save-meal")
async def api_save_meal(request: Request, payload: SaveMealRequest):
    user = _require_user(request)
    if not payload.items:
        raise HTTPException(status_code=400, detail="Items are required")

    saved = save_meal(user["id"], payload.items, date_str=payload.date)
    return {
        "saved": saved,
        "history": get_nutrition_history(user["id"]),
        "settings": get_user_settings(user["id"]),
        "stats": get_statistics_bundle(user["id"]),
        "nutrition_history": get_nutrition_history(user["id"]),
    }


@app.post("/api/hundred")
async def api_hundred(request: Request, payload: TextActionRequest):
    user = _require_user(request)
    text = _require_text(payload.text)
    result = await analyze_hundred(user["id"], text)
    log_exchange(user["id"], text, result["text"], source="web")
    return {
        "reply_text": result["text"],
        "messages": get_chat_history(user["id"]),
    }


@app.post("/api/online")
async def api_online(request: Request, payload: TextActionRequest):
    user = _require_user(request)
    text = _require_text(payload.text)
    result = await process_online_interaction(user["id"], text, source="web_assistant")
    return {
        "reply_text": result["reply_text"],
        "messages": result["history"],
    }


@app.post("/api/photo/analyze")
async def api_photo_analyze(
    request: Request,
    photo: UploadFile = File(...),
    caption: str = Form(default=""),
):
    user = _require_user(request)
    raw_bytes = await photo.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Image is required")

    photo_data_url = _photo_to_data_url(photo, raw_bytes)
    try:
        result = await analyze_photo(user["id"], photo_data_url, caption=caption.strip())
    except Exception as exc:
        logging.exception("Photo analysis failed for user %s: %s", user["id"], exc)
        raise HTTPException(
            status_code=502,
            detail="Не получилось обработать фото. Попробуй выбрать его ещё раз или отправить другое.",
        ) from exc
    return result


@app.post("/api/import-legacy-storage")
async def api_import_legacy_storage(
    request: Request,
    archive: UploadFile = File(...),
):
    user = _require_user(request)
    raw_bytes = await archive.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="ZIP archive is required")

    try:
        summary = import_legacy_storage_zip(raw_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "import": summary,
        "dashboard": _dashboard_payload(user),
    }


@app.post("/api/settings/limit")
async def api_settings_limit(request: Request, payload: LimitRequest):
    user = _require_user(request)
    try:
        normalized_limit = validate_limit(payload.daily_limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    settings = set_daily_limit(user["id"], normalized_limit)
    return {
        "settings": settings,
        "stats": get_statistics_bundle(user["id"]),
    }


@app.post("/api/settings/mode")
async def api_settings_mode(request: Request, payload: ModeRequest):
    user = _require_user(request)
    try:
        settings = set_model_mode(user["id"], payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"settings": settings}


@app.post("/api/settings/language")
async def api_settings_language(request: Request, payload: LanguageRequest):
    user = _require_user(request)
    try:
        if payload.generate:
            settings = await generate_and_set_language(user["id"], payload.language_code)
        else:
            settings = set_language(user["id"], payload.language_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    language_code = settings["language_code"]
    return {
        "settings": settings,
        "available_modes": _mode_options(language_code),
        "stats": get_statistics_bundle(user["id"]),
        "locale": locale_payload(language_code),
    }


@app.post("/api/settings/assistant-name")
async def api_settings_assistant_name(request: Request, payload: AssistantNameRequest):
    user = _require_user(request)
    try:
        settings = set_assistant_name(user["id"], payload.assistant_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"settings": settings}


@app.delete("/api/nutrition-history/{date_key}/{meal_number}")
async def api_delete_meal(request: Request, date_key: str, meal_number: int):
    user = _require_user(request)
    delete_meal(user["id"], date_key, meal_number)
    return {
        "history": get_nutrition_history(user["id"]),
        "stats": get_statistics_bundle(user["id"]),
    }


@app.delete("/api/nutrition-history/{date_key}/{meal_number}/{item_index}")
async def api_delete_item(request: Request, date_key: str, meal_number: int, item_index: int):
    user = _require_user(request)
    try:
        delete_item(user["id"], date_key, meal_number, item_index)
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "history": get_nutrition_history(user["id"]),
        "stats": get_statistics_bundle(user["id"]),
    }


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
