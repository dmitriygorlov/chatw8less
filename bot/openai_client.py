from openai import AsyncOpenAI

from bot.config import (
    DEFAULT_GPT_MODEL,
    DEFAULT_VISION_MODEL,
    OPENAI_API_KEY,
    OPENAI_MAX_TOKENS,
    OPENAI_PHOTO_DETAIL,
    SYSTEM_PROMPT_FUNC,
    SYSTEM_PROMPT_VISION,
    SYSTEM_PROMPT_ONLINE,
)


# Глобальный асинхронный клиент, инициализирован один раз
client = AsyncOpenAI(api_key=OPENAI_API_KEY)


def _apply_reasoning(request_params: dict, reasoning_effort: str | None) -> None:
    if reasoning_effort:
        request_params["reasoning"] = {"effort": reasoning_effort}


async def get_openai_response(
    user_message: str,
    function_name: str | None = None,
    system_prompt: str = SYSTEM_PROMPT_FUNC,
    model: str | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
):
    """Получение ответа от OpenAI (асинхронно).

    При передаче ``function_name`` используется JSON Schema для строгого
    структурированного ответа.
    """

    json_schema = {
        "name": "log_nutrition_data",
        "schema": {
            "type": "object",
            "properties": {
                "intro_message": {
                    "type": "string",
                    "description": (
                        "A brief introductory message in the language of the query, "
                        "e.g. 'Вот твой подробный расчет калорий и БЖУ для приема пищи:'"
                    ),
                },
                "items": {
                    "type": "array",
                    "description": "A list of food items with their quantities and nutritional details.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "amount_grams": {"type": "number"},
                            "calories": {"type": "number"},
                            "protein": {"type": "number"},
                            "fat": {"type": "number"},
                            "carbs": {"type": "number"},
                        },
                        "required": [
                            "name",
                            "amount_grams",
                            "calories",
                            "protein",
                            "fat",
                            "carbs",
                        ],
                    },
                },
                "fun_addition": {
                    "type": "string",
                    "description": (
                        "A fun and varied addition that ‘colors’ the list: could be a joke, a quirky fitness mini‑challenge, "
                        "a surprising food fact or a quick cooking hack—anything to make the summary more engaging."
                    ),
                },
            },
            "required": [
                "intro_message",
                "items",
                "fun_addition",
            ],
        },
    }

    request_params = {
        "model": model or DEFAULT_GPT_MODEL,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {"role": "user", "content": [{"type": "input_text", "text": user_message}]},
        ],
        "max_output_tokens": max_tokens or OPENAI_MAX_TOKENS,
    }
    _apply_reasoning(request_params, reasoning_effort)

    if function_name:
        request_params["tools"] = [
            {
                "type": "function",
                "name": json_schema["name"],
                "parameters": json_schema["schema"],
            }
        ]
        request_params["tool_choice"] = {
            "type": "function",
            "name": json_schema["name"],
        }

    response = await client.responses.create(**request_params)
    return response


async def get_openai_vision_response(
    image_url: str,
    user_comment: str = "",
    prompt: str = SYSTEM_PROMPT_VISION,
    model: str | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
):
    if user_comment:
        user_text = (
            f'User added a comment to this photo: "{user_comment}". '
            "If this helps you clarify what's on the image, use it as additional context."
        )
    else:
        user_text = "No additional comment from the user."

    input_data = [
        {"role": "system", "content": [{"type": "input_text", "text": prompt}]},
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": user_text},
                {
                    "type": "input_image",
                    "image_url": image_url,
                    "detail": OPENAI_PHOTO_DETAIL,
                },
            ],
        },
    ]

    request_params = {
        "model": model or DEFAULT_VISION_MODEL,
        "input": input_data,
        "max_output_tokens": max_tokens or OPENAI_MAX_TOKENS,
    }
    _apply_reasoning(request_params, reasoning_effort)

    response = await client.responses.create(**request_params)
    return response


async def get_openai_online_response(
    user_query: str,
    prompt: str = SYSTEM_PROMPT_ONLINE,
    model: str | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
):
    """Получение ответа от OpenAI с веб-поиском (асинхронно).

    Используется для запросов о конкретных брендах, актуальной нутриционной
    информации и фитнес-вопросов, требующих доступа к интернету.
    """

    input_data = [
        {"role": "system", "content": [{"type": "input_text", "text": prompt}]},
        {"role": "user", "content": [{"type": "input_text", "text": user_query}]},
    ]

    request_params = {
        "model": model or DEFAULT_GPT_MODEL,
        "input": input_data,
        "max_output_tokens": max_tokens or OPENAI_MAX_TOKENS,
        "tools": [{"type": "web_search"}],  # Enable web search
    }
    _apply_reasoning(request_params, reasoning_effort)

    response = await client.responses.create(**request_params)
    return response


async def generate_locale_response(
    language_request: str,
    source_messages: dict[str, str],
    model: str | None = None,
):
    keys_json = __import__("json").dumps(source_messages, ensure_ascii=False, indent=2)
    prompt = (
        "Create a UI localization JSON for the requested language.\n"
        f"Requested language: {language_request}\n\n"
        "Rules:\n"
        "- Infer a concise BCP-47 language code, such as it, de, fr, es, pt-BR.\n"
        "- Translate every value, preserving placeholders like {date}, {count}, {support_contact} exactly.\n"
        "- Preserve markdown, line breaks, command names like /help, and emoji.\n"
        "- Do not translate JSON keys.\n"
        "- Return only through the function call.\n\n"
        f"Source English JSON:\n{keys_json}"
    )
    schema = {
        "type": "object",
        "properties": {
            "language_code": {
                "type": "string",
                "description": "BCP-47 language code, lowercase where appropriate.",
            },
            "native_name": {
                "type": "string",
                "description": "Language name in that language, for UI display.",
            },
            "messages": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["language_code", "native_name", "messages"],
    }
    return await client.responses.create(
        model=model or DEFAULT_GPT_MODEL,
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": "You generate complete app localization dictionaries."}],
            },
            {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
        ],
        max_output_tokens=max(OPENAI_MAX_TOKENS, 12000),
        tools=[
            {
                "type": "function",
                "name": "create_locale",
                "parameters": schema,
            }
        ],
        tool_choice={"type": "function", "name": "create_locale"},
    )
