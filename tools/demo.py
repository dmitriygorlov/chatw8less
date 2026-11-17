"""Run a synthetic meal through the real in-process web/application/storage flow."""
import json
import inspect
from types import SimpleNamespace
from unittest.mock import patch

from tools.offline import isolated_runtime

INPUT = "Demo porridge 100 g"
ITEM = {"name": "Demo porridge (fictional)", "amount_grams": 100,
        "calories": 120, "protein": 4, "fat": 3, "carbs": 19}


def run_scenario():
    # Imports must follow isolation: config normally loads .env at import time.
    from fastapi.testclient import TestClient
    from bot import db, openai_client
    import web_app

    calls = []
    sdk_signature = inspect.signature(openai_client.client.responses.create)

    async def fixture_response(**request):
        sdk_signature.bind(**request)
        calls.append(request)
        assert request["tools"][0]["name"] == "log_nutrition_data"
        return SimpleNamespace(status="completed", usage=None, output=[
            SimpleNamespace(type="function_call", name="log_nutrition_data",
                            status="completed", arguments=json.dumps({
                                "intro_message": "DEMO: fixed fictional data; not an AI estimate.",
                                "items": [ITEM], "fun_addition": ""}))])

    with patch.object(openai_client.client.responses, "create", fixture_response):
        with TestClient(web_app.app) as client:
            db.create_or_update_user("Fictional Demo User", "demo-only-phrase",
                                     user_id="demo", language_code="en")
            assert client.get("/api/nutrition-history").status_code == 401
            login = client.post("/login", data={"phrase": "demo-only-phrase"}, follow_redirects=False)
            assert login.status_code == 303
            response = client.post("/api/analyze-text", json={"text": INPUT, "save": False})
            assert response.status_code == 200
            draft = response.json()
            assert draft["saved"] is None
            assert draft["items"] == [ITEM]
            assert len(draft["messages"]) == 2
            assert "DEMO:" in draft["reply_text"]
            assert client.get("/api/nutrition-history").json()["history"] == []
            saved = client.post("/api/save-meal", json={"items": draft["items"]})
            assert saved.status_code == 200
            history = client.get("/api/nutrition-history").json()["history"]
            assert history[0]["meals"][0]["items"] == [ITEM]
            assert len(calls) == 1
            return {"mode": "DEMO - fixed fictional fixture, not an AI answer",
                    "input": INPUT, "items": draft["items"],
                    "checks": ["authentication", "request construction", "response parsing",
                               "chat history", "draft is not saved", "explicit save and readback"],
                    "real_api_calls": 0}


def main():
    with isolated_runtime():
        result = run_scenario()
    print(json.dumps(result, indent=2))
    print("PASS: offline demo; temporary data removed.")


if __name__ == "__main__":
    main()
