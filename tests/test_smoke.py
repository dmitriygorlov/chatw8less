import json
import asyncio
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

import bot.chat_service as chat_service
import bot.app_services as app_services
import bot.config as config
import bot.db as db
import bot.handlers as handlers
import bot.i18n as i18n
import bot.utils as utils
import web_app


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    db_path = tmp_path / "chatw8less.sqlite3"
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "DATABASE_PATH", str(db_path), raising=False)
    monkeypatch.setattr(db, "DATABASE_PATH", str(db_path), raising=False)
    monkeypatch.setattr(utils, "STORAGE_DIR", str(storage_dir), raising=False)
    monkeypatch.setattr(i18n, "GENERATED_LOCALES_DIR", storage_dir / "generated_locales", raising=False)
    monkeypatch.setattr(web_app, "TELEGRAM_API_TOKEN", "", raising=False)
    i18n.load_locale.cache_clear()

    monkeypatch.setenv("RUN_TELEGRAM_IN_WEB", "false")
    monkeypatch.setenv("SITE_URL", "http://testserver")
    monkeypatch.delenv("WEB_COOKIE_SECURE", raising=False)

    db.init_db()
    return {
        "db_path": db_path,
        "storage_dir": storage_dir,
    }


def create_client(base_url="http://testserver"):
    return TestClient(web_app.app, base_url=base_url)


def create_user(user_id="u1", phrase="secret phrase", name="Test User"):
    return db.create_or_update_user(name, phrase, user_id=user_id)


def test_login_success_local_cookie_and_history_access(isolated_env):
    create_user()

    with create_client() as client:
        response = client.post("/login", data={"phrase": "secret phrase"}, follow_redirects=False)
        assert response.status_code == 303
        set_cookie = response.headers["set-cookie"]
        assert "chatw8less_session=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Secure" not in set_cookie

        history_response = client.get("/api/history")
        assert history_response.status_code == 200
        payload = history_response.json()
        assert payload["user"]["id"] == "u1"
        assert payload["messages"] == []
        assert history_response.headers["cache-control"].startswith("no-store")


def test_dashboard_disables_cache_and_versions_static_assets(isolated_env):
    create_user(user_id="cache_user", phrase="cache phrase")

    with create_client() as client:
        client.post("/login", data={"phrase": "cache phrase"}, follow_redirects=False)
        response = client.get("/")

        assert response.status_code == 200
        assert response.headers["cache-control"].startswith("no-store")
        assert 'href="/static/styles.css?v=' in response.text
        assert 'src="/static/app.js?v=' in response.text


def test_login_rejects_wrong_phrase(isolated_env):
    create_user()

    with create_client() as client:
        response = client.post("/login", data={"phrase": "wrong phrase"}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/?login_error=1"
        assert "set-cookie" not in response.headers


def test_favicon_redirects_to_static_asset(isolated_env):
    with create_client() as client:
        response = client.get("/favicon.ico", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/static/favicon.png"


def test_login_sets_secure_cookie_in_production_mode(isolated_env, monkeypatch):
    create_user(user_id="u2", phrase="prod phrase")
    monkeypatch.setenv("WEB_COOKIE_SECURE", "true")

    with create_client(base_url="https://testserver") as client:
        response = client.post("/login", data={"phrase": "prod phrase"}, follow_redirects=False)
        assert response.status_code == 303
        assert "Secure" in response.headers["set-cookie"]


def test_legacy_meal_data_is_merged_into_sqlite_even_if_user_row_exists(isolated_env):
    user_id = "legacy_user"
    db.ensure_user(user_id)

    user_dir = Path(isolated_env["storage_dir"]) / user_id
    day_dir = user_dir / "2025" / "05"
    day_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "meta.json").write_text(
        json.dumps({"daily_limit": 1700, "model_mode": "smart"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (day_dir / "06.json").write_text(
        json.dumps(
            {
                "1": [
                    {
                        "name": "legacy rice",
                        "amount_grams": 100,
                        "calories": 130,
                        "protein": 2.5,
                        "fat": 0.3,
                        "carbs": 28,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    data = utils.load_user_data_new(user_id)

    assert data["daily_limit"] == 1700
    assert data["model_mode"] == "smart"
    assert data["2025-05-06"]["1"][0]["name"] == "legacy rice"

    sqlite_data = db.load_user_state(user_id)
    assert sqlite_data["2025-05-06"]["1"][0]["name"] == "legacy rice"


def test_shared_history_is_saved_and_read_via_web_api(isolated_env, monkeypatch):
    create_user(user_id="shared_user", phrase="shared phrase")

    async def fake_analyze_food_text(user_id: str, text: str):
        return {
            "display_text": f"Ответ для {user_id}: {text}",
            "items": [
                {
                    "name": "apple",
                    "amount_grams": 120,
                    "calories": 62,
                    "protein": 0.3,
                    "fat": 0.2,
                    "carbs": 15,
                }
            ],
        }

    monkeypatch.setattr(chat_service, "analyze_food_text", fake_analyze_food_text)

    with create_client() as client:
        login_response = client.post(
            "/login",
            data={"phrase": "shared phrase"},
            follow_redirects=False,
        )
        assert login_response.status_code == 303

        message_response = client.post("/api/message", json={"text": "70 грамм яблока"})
        assert message_response.status_code == 200
        payload = message_response.json()
        assert payload["saved"]["date"]
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "user"
        assert payload["messages"][1]["role"] == "assistant"

        history_response = client.get("/api/history")
        assert history_response.status_code == 200
        history_payload = history_response.json()
        assert len(history_payload["messages"]) == 2
        assert history_payload["messages"][0]["source"] == "web"
        assert history_payload["messages"][1]["source"] == "web"


def test_food_interaction_does_not_send_previous_messages_to_model(isolated_env, monkeypatch):
    create_user(user_id="context_user", phrase="context phrase")
    db.add_message("context_user", "user", "assistant question", "web_assistant")
    db.add_message("context_user", "assistant", "assistant answer", "web_assistant")
    db.add_message("context_user", "user", "bread and wine", "web")
    db.add_message("context_user", "assistant", "bread and wine calculation", "web")
    seen_inputs = []

    async def fake_analyze_food_text(user_id: str, text: str):
        seen_inputs.append(text)
        return {
            "display_text": "ok",
            "items": [{
                "name": "sausage",
                "amount_grams": 100,
                "calories": 280,
                "protein": 12,
                "fat": 25,
                "carbs": 2,
            }],
        }

    monkeypatch.setattr(chat_service, "analyze_food_text", fake_analyze_food_text)

    result = asyncio.run(
        chat_service.process_text_interaction(
            "context_user",
            "sausage",
            source="web",
        )
    )

    assert result["items"][0]["name"] == "sausage"
    assert seen_inputs == ["sausage"]


def test_assistant_context_uses_configured_dialog_count(isolated_env):
    create_user(user_id="assistant_context_user", phrase="assistant context phrase")
    db.add_message("assistant_context_user", "user", "old question", "web_assistant")
    db.add_message("assistant_context_user", "assistant", "old answer", "web_assistant")
    db.add_message("assistant_context_user", "user", "latest question", "web_assistant")
    db.add_message("assistant_context_user", "assistant", "latest answer", "web_assistant")

    contextual = chat_service._build_contextual_input(
        "assistant_context_user",
        "follow up",
        dialog_limit=1,
        source="web_assistant",
    )

    assert "latest question" in contextual
    assert "latest answer" in contextual
    assert "old question" not in contextual
    assert "old answer" not in contextual


def test_online_web_chat_uses_previous_messages_as_context(isolated_env, monkeypatch):
    create_user(user_id="online_user", phrase="online phrase")
    seen_inputs = []

    async def fake_analyze_online(user_id: str, text: str):
        seen_inputs.append(text)
        return {"text": f"assistant reply {len(seen_inputs)}"}

    monkeypatch.setattr(chat_service, "analyze_online", fake_analyze_online)

    with create_client() as client:
        client.post("/login", data={"phrase": "online phrase"}, follow_redirects=False)

        first_response = client.post("/api/online", json={"text": "What can I cook?"})
        assert first_response.status_code == 200
        assert seen_inputs[-1] == "What can I cook?"
        db.add_message("online_user", role="user", content="100 g banana", source="web")
        db.add_message("online_user", role="assistant", content="Banana nutrition", source="web")

        second_response = client.post("/api/online", json={"text": "Make it lighter"})
        assert second_response.status_code == 200
        second_payload = second_response.json()
        assert "Conversation context" in seen_inputs[-1]
        assert "short follow-up" in seen_inputs[-1]
        assert "User: What can I cook?" in seen_inputs[-1]
        assert "Assistant: assistant reply 1" in seen_inputs[-1]
        assert "Current user message (answer this): Make it lighter" in seen_inputs[-1]
        assert "100 g banana" not in seen_inputs[-1]
        assert len(second_payload["messages"]) == 6
        assert second_payload["messages"][-2]["content"] == "Make it lighter"
        assert second_payload["messages"][-1]["content"] == "assistant reply 2"
        assert second_payload["messages"][-1]["source"] == "web_assistant"


def test_online_followup_keeps_store_context(isolated_env, monkeypatch):
    create_user(user_id="followup_user", phrase="followup phrase")
    seen_inputs = []

    async def fake_analyze_online(user_id: str, text: str):
        seen_inputs.append(text)
        return {"text": "ok"}

    monkeypatch.setattr(chat_service, "analyze_online", fake_analyze_online)

    with create_client() as client:
        client.post("/login", data={"phrase": "followup phrase"}, follow_redirects=False)

        client.post("/api/online", json={"text": "Что во вкусвил самое вкусное?"})
        response = client.post("/api/online", json={"text": "А что скажешь про манты?"})

        assert response.status_code == 200
        assert "ВкусВилл" in seen_inputs[-1] or "вкусвил" in seen_inputs[-1]
        assert "А что скажешь про манты?" in seen_inputs[-1]
        assert "store" in seen_inputs[-1]


def test_online_analysis_uses_dedicated_assistant_model(isolated_env, monkeypatch):
    create_user(user_id="assistant_model_user", phrase="assistant model phrase")
    seen = {}

    class FakeResponse:
        output_text = "assistant answer"
        output = []
        usage = None
        status = "completed"

    async def fake_get_openai_online_response(text, **kwargs):
        seen["text"] = text
        seen.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(app_services, "GPT_MODEL_ASSISTANT", "gpt-assistant")
    monkeypatch.setattr(app_services, "GPT_REASONING_ASSISTANT", "low")
    monkeypatch.setattr(app_services, "MAX_TOKENS_ASSISTANT", 5000)
    monkeypatch.setattr(
        app_services,
        "get_openai_online_response",
        fake_get_openai_online_response,
    )

    result = asyncio.run(
        app_services.analyze_online("assistant_model_user", "What should I eat?")
    )

    assert seen["model"] == "gpt-assistant"
    assert seen["reasoning_effort"] == "low"
    assert seen["max_tokens"] == 5000
    assert result["mode"] == "assistant"
    assert result["model_label"] == "gpt-assistant"


def test_web_food_reply_without_save_and_manual_save_flow(isolated_env, monkeypatch):
    create_user(user_id="food_user", phrase="food phrase")

    async def fake_analyze_food_text(user_id: str, text: str):
        return {
            "display_text": f"Разбор для {user_id}: {text}",
            "items": [
                {
                    "name": "banana",
                    "amount_grams": 100,
                    "calories": 89,
                    "protein": 1.1,
                    "fat": 0.3,
                    "carbs": 23,
                }
            ],
        }

    monkeypatch.setattr(chat_service, "analyze_food_text", fake_analyze_food_text)

    with create_client() as client:
        client.post("/login", data={"phrase": "food phrase"}, follow_redirects=False)

        analyze_response = client.post(
            "/api/analyze-text",
            json={"text": "100 грамм банана", "save": False},
        )
        assert analyze_response.status_code == 200
        analyze_payload = analyze_response.json()
        assert analyze_payload["saved"] is None
        assert len(analyze_payload["messages"]) == 2

        empty_history = client.get("/api/nutrition-history")
        assert empty_history.status_code == 200
        assert empty_history.json()["history"] == []

        save_response = client.post(
            "/api/save-meal",
            json={"items": analyze_payload["items"]},
        )
        assert save_response.status_code == 200
        save_payload = save_response.json()
        assert save_payload["saved"]["meal_number"] == 1
        assert save_payload["history"][0]["meals"][0]["items"][0]["name"] == "banana"


def test_food_analysis_retries_when_model_returns_no_function_payload(isolated_env, monkeypatch):
    create_user(user_id="retry_user", phrase="retry phrase")
    calls = []

    class FakeCall:
        type = "function_call"
        name = "log_nutrition_data"
        status = "completed"

        def __init__(self, arguments):
            self.arguments = arguments

    class FakeResponse:
        usage = None
        status = "completed"

        def __init__(self, output):
            self.output = output

    async def fake_get_openai_response(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return FakeResponse([])
        return FakeResponse([
            FakeCall(json.dumps({
                "intro_message": "ok",
                "items": [
                    {
                        "name": "banana",
                        "amount_grams": 100,
                        "calories": 89,
                        "protein": 1.1,
                        "fat": 0.3,
                        "carbs": 23,
                    }
                ],
                "fun_addition": "done",
            }))
        ])

    monkeypatch.setattr(
        app_services,
        "get_user_model_config",
        lambda user_id: ("fast", {
            "model": "gpt-5-nano",
            "vision_model": "gpt-5-nano",
            "max_tokens": 2048,
            "reasoning_effort": "minimal",
        }),
    )
    monkeypatch.setattr(app_services, "get_openai_response", fake_get_openai_response)

    result = asyncio.run(app_services.analyze_food_text("retry_user", "100 g banana"))

    assert result["items"][0]["name"] == "banana"
    assert len(calls) == 2
    assert calls[0]["max_tokens"] == 2048
    assert calls[1]["max_tokens"] == 4096
    assert calls[1]["reasoning_effort"] == "minimal"


def test_food_analysis_retries_suspicious_all_zero_nutrition(isolated_env, monkeypatch):
    create_user(user_id="zero_retry_user", phrase="zero retry phrase")
    calls = []

    class FakeCall:
        type = "function_call"
        name = "log_nutrition_data"
        status = "completed"

        def __init__(self, items):
            self.arguments = json.dumps({
                "intro_message": "ok",
                "items": items,
                "fun_addition": "done",
            })

    class FakeResponse:
        usage = None
        status = "completed"

        def __init__(self, items):
            self.output = [FakeCall(items)]

    async def fake_get_openai_response(text, **kwargs):
        calls.append((text, kwargs))
        if len(calls) == 1:
            return FakeResponse([{
                "name": "cherries",
                "amount_grams": 150,
                "calories": 0,
                "protein": 0,
                "fat": 0,
                "carbs": 0,
            }])
        return FakeResponse([{
            "name": "cherries",
            "amount_grams": 150,
            "calories": 95,
            "protein": 1.6,
            "fat": 0.3,
            "carbs": 24,
        }])

    monkeypatch.setattr(
        app_services,
        "get_user_model_config",
        lambda user_id: ("fast", {
            "model": "gpt-5-nano",
            "vision_model": "gpt-5-nano",
            "max_tokens": 2048,
            "reasoning_effort": "minimal",
        }),
    )
    monkeypatch.setattr(app_services, "get_openai_response", fake_get_openai_response)

    result = asyncio.run(app_services.analyze_food_text("zero_retry_user", "150 g cherries"))

    assert result["items"][0]["calories"] == 95
    assert len(calls) == 2
    assert "Correction required" in calls[1][0]
    assert calls[1][1]["max_tokens"] == 4096


def test_reasoning_effort_none_aliases_to_minimal(monkeypatch):
    monkeypatch.setenv("GPT_REASONING_FAST", "none")
    assert config._reasoning_effort_from_env("GPT_REASONING_FAST") == "minimal"


def test_web_settings_endpoints_update_limit_and_mode(isolated_env):
    create_user(user_id="settings_user", phrase="settings phrase")

    with create_client() as client:
        client.post("/login", data={"phrase": "settings phrase"}, follow_redirects=False)

        limit_response = client.post("/api/settings/limit", json={"daily_limit": 1800})
        assert limit_response.status_code == 200
        assert limit_response.json()["settings"]["daily_limit"] == 1800

        mode_response = client.post("/api/settings/mode", json={"mode": "smart"})
        assert mode_response.status_code == 200
        assert mode_response.json()["settings"]["mode"] == "smart"

        language_response = client.post(
            "/api/settings/language",
            json={"language_code": "en"},
        )
        assert language_response.status_code == 200
        language_payload = language_response.json()
        assert language_payload["settings"]["language_code"] == "en"
        assert language_payload["locale"]["language_code"] == "en"
        assert language_payload["locale"]["messages"]["web.login_button"] == "Sign in"

        assistant_response = client.post(
            "/api/settings/assistant-name",
            json={"assistant_name": "Mila"},
        )
        assert assistant_response.status_code == 200
        assert assistant_response.json()["settings"]["assistant_name"] == "Mila"

        invalid_assistant_response = client.post(
            "/api/settings/assistant-name",
            json={"assistant_name": ""},
        )
        assert invalid_assistant_response.status_code == 400

        invalid_response = client.post(
            "/api/settings/language",
            json={"language_code": "de"},
        )
        assert invalid_response.status_code == 400


def test_existing_users_receive_russian_language_on_db_migration(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.sqlite3"
    monkeypatch.setattr(config, "DATABASE_PATH", str(db_path), raising=False)
    monkeypatch.setattr(db, "DATABASE_PATH", str(db_path), raising=False)

    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            display_name TEXT,
            telegram_user_id TEXT UNIQUE,
            passphrase_salt TEXT,
            passphrase_hash TEXT,
            daily_limit INTEGER,
            model_mode TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO users (id, display_name, telegram_user_id, model_mode, created_at, updated_at)
        VALUES ('legacy', 'Legacy', 'legacy', 'fast', '2025-01-01', '2025-01-01')
        """
    )
    conn.commit()
    conn.close()

    db.init_db()
    assert db.get_user("legacy")["language_code"] == "ru"
    assert db.get_user("legacy")["assistant_name"] == "Alex"


def test_new_telegram_user_uses_account_language_or_english(isolated_env):
    supported = db.get_or_create_user_for_telegram(
        "telegram_sr",
        display_name="Serbian User",
        language_code="sr-RS",
    )
    fallback = db.get_or_create_user_for_telegram(
        "telegram_unknown",
        display_name="Unknown User",
        language_code="de-DE",
    )

    assert supported["language_code"] == "sr"
    assert fallback["language_code"] == "en"
    assert supported["assistant_name"] == "Alex"


def test_language_endpoint_can_generate_new_locale(isolated_env, monkeypatch):
    create_user(user_id="gen_user", phrase="gen phrase")
    source = app_services.load_locale("en")

    class FakeCall:
        type = "function_call"
        name = "create_locale"
        status = "completed"

        def __init__(self, arguments):
            self.arguments = arguments

    class FakeResponse:
        usage = None
        status = "completed"

        def __init__(self, arguments):
            self.output = [FakeCall(arguments)]

    async def fake_generate_locale_response(language_request, source_messages, model=None):
        assert language_request == "Italian"
        messages = {key: f"it:{value}" for key, value in source.items()}
        for key, value in source.items():
            for placeholder in app_services._placeholders(value):
                if placeholder not in messages[key]:
                    messages[key] += f" {placeholder}"
        return FakeResponse(
            json.dumps(
                {
                    "language_code": "it",
                    "native_name": "Italiano",
                    "messages": messages,
                },
                ensure_ascii=False,
            )
        )

    monkeypatch.setattr(app_services, "generate_locale_response", fake_generate_locale_response)

    with create_client() as client:
        client.post("/login", data={"phrase": "gen phrase"}, follow_redirects=False)
        response = client.post(
            "/api/settings/language",
            json={"language_code": "Italian", "generate": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["language_code"] == "it"
    assert payload["locale"]["language_code"] == "it"
    assert any(language["code"] == "it" for language in payload["locale"]["languages"])


def test_legacy_storage_zip_import_endpoint_merges_history(isolated_env):
    create_user(user_id="zip_user", phrase="zip phrase")

    archive_buffer = BytesIO()
    with ZipFile(archive_buffer, "w") as archive:
        archive.writestr(
            "storage/zip_user/meta.json",
            json.dumps({"daily_limit": 1650, "model_mode": "smart"}, ensure_ascii=False),
        )
        archive.writestr(
            "storage/zip_user/2025/04/06.json",
            json.dumps(
                {
                    "1": [
                        {
                            "name": "legacy soup",
                            "amount_grams": 250,
                            "calories": 180,
                            "protein": 10,
                            "fat": 6,
                            "carbs": 20,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        )

    with create_client() as client:
        client.post("/login", data={"phrase": "zip phrase"}, follow_redirects=False)
        response = client.post(
            "/api/import-legacy-storage",
            files={"archive": ("storage.zip", archive_buffer.getvalue(), "application/zip")},
        )

        assert response.status_code == 200
        payload = response.json()
        assert "zip_user" in payload["import"]["imported_user_ids"]
        assert payload["dashboard"]["settings"]["daily_limit"] == 1650
        assert payload["dashboard"]["nutrition_history"][0]["meals"][0]["items"][0]["name"] == "legacy soup"

        sqlite_data = db.load_user_state("zip_user")
        assert sqlite_data["2025-04-06"]["1"][0]["name"] == "legacy soup"


class FakeState:
    def __init__(self):
        self.state = None

    async def clear(self):
        self.state = None

    async def set_state(self, value):
        self.state = value


class FakeFromUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeMessage:
    def __init__(self, user_id, text=None):
        self.from_user = FakeFromUser(user_id)
        self.text = text


def test_telegram_user_can_set_web_passphrase_without_manual_cli(isolated_env, monkeypatch):
    user_id = "telegram_user_1"
    db.ensure_user(user_id, telegram_user_id=user_id, display_name="Telegram User")
    fake_state = FakeState()
    sent_messages = []

    async def fake_send_long_message(_message, text, **kwargs):
        sent_messages.append({"text": text, "kwargs": kwargs})
        return None

    monkeypatch.setattr(handlers, "SITE_URL", "", raising=False)
    monkeypatch.setattr(handlers, "send_long_message", fake_send_long_message)

    asyncio.run(handlers.site_command(FakeMessage(user_id), fake_state))
    assert "Кодовая фраза ещё не настроена" in sent_messages[-1]["text"]

    asyncio.run(handlers.site_phrase_handler(FakeMessage(user_id, text="my phrase 123"), fake_state))
    assert db.user_has_passphrase(user_id) is True
    linked_user = db.get_user_by_passphrase("my phrase 123")
    assert linked_user is not None
    assert linked_user["id"] == user_id
    assert "Кодовая фраза сохранена" in sent_messages[-1]["text"]
