"""Exercise the installed SDK's serialization/parsing with an in-memory transport."""
import asyncio
import json

import httpx2
from openai import AsyncOpenAI

from bot import openai_client


def test_installed_sdk_supports_application_requests(monkeypatch):
    requests = []

    def respond(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/responses"
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json={
            "id": "resp_fictional", "object": "response", "created_at": 0,
            "status": "completed", "model": "fixture-model", "output": [],
            "usage": None,
        })

    async def exercise():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as transport:
            async with AsyncOpenAI(api_key="offline-placeholder-not-a-key", http_client=transport) as client:
                monkeypatch.setattr(openai_client, "client", client)
                responses = [
                    await openai_client.get_openai_response("Fictional meal", function_name="log_nutrition_data"),
                    await openai_client.get_openai_vision_response("data:image/png;base64,AAAA"),
                    await openai_client.get_openai_online_response("Fictional question"),
                    await openai_client.generate_locale_response("French", {"hello": "Hello"}),
                ]
                assert all(response.status == "completed" for response in responses)

    asyncio.run(exercise())
    assert len(requests) == 4
    assert requests[0]["tools"][0]["name"] == "log_nutrition_data"
    assert requests[2]["tools"] == [{"type": "web_search"}]
    assert requests[3]["tools"][0]["name"] == "create_locale"
