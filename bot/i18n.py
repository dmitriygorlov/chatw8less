import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
LOCALES_DIR = BASE_DIR / "locales"
LEGACY_GENERATED_LOCALES_DIR = BASE_DIR / "storage" / "generated_locales"
GENERATED_LOCALES_DIR = Path(
    os.getenv("GENERATED_LOCALES_DIR") or LOCALES_DIR
)
DEFAULT_LANGUAGE = "en"
EXISTING_USER_DEFAULT_LANGUAGE = "ru"
SUPPORTED_LANGUAGES = ("ru", "en", "sr")


LANGUAGE_NAMES = {
    "ru": "Русский",
    "en": "English",
    "sr": "Srpski",
}


def _safe_language_code(language_code: str) -> str:
    normalized = language_code.strip().lower().replace("_", "-")
    normalized = re.sub(r"[^a-z0-9-]", "", normalized)
    parts = [part for part in normalized.split("-") if part]
    if not parts:
        return ""
    return "-".join(parts[:3])


def _locale_path(language_code: str) -> Path | None:
    safe_code = _safe_language_code(language_code)
    if not safe_code:
        return None
    built_in = LOCALES_DIR / f"{safe_code}.json"
    if built_in.exists():
        return built_in
    generated = GENERATED_LOCALES_DIR / f"{safe_code}.json"
    if generated.exists():
        return generated
    legacy_generated = LEGACY_GENERATED_LOCALES_DIR / f"{safe_code}.json"
    if legacy_generated.exists():
        return legacy_generated
    return None


def generated_language_codes() -> list[str]:
    codes = set()
    for directory in (GENERATED_LOCALES_DIR, LEGACY_GENERATED_LOCALES_DIR):
        if not directory.exists():
            continue
        codes.update(
            path.stem
            for path in directory.glob("*.json")
            if _safe_language_code(path.stem) == path.stem
        )
    return sorted(codes)


def supported_language_codes() -> tuple[str, ...]:
    return tuple(dict.fromkeys([*SUPPORTED_LANGUAGES, *generated_language_codes()]))


@lru_cache(maxsize=None)
def load_locale(language_code: str) -> dict[str, str]:
    normalized = normalize_language_code(language_code, fallback=DEFAULT_LANGUAGE)
    path = _locale_path(normalized) or LOCALES_DIR / f"{DEFAULT_LANGUAGE}.json"
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def normalize_language_code(
    language_code: str | None,
    fallback: str = DEFAULT_LANGUAGE,
) -> str:
    if not language_code:
        return fallback
    normalized = _safe_language_code(language_code)
    base = normalized.split("-", maxsplit=1)[0]
    codes = supported_language_codes()
    if normalized in codes:
        return normalized
    if base in codes:
        return base
    return fallback


def language_options() -> list[dict[str, str]]:
    options = []
    for code in supported_language_codes():
        messages = load_locale(code)
        options.append(
            {
                "code": code,
                "name": messages.get("language.native_name") or LANGUAGE_NAMES.get(code) or code,
            }
        )
    return options


def language_name(language_code: str | None) -> str:
    normalized = normalize_language_code(language_code)
    messages = load_locale(normalized)
    return messages.get("language.native_name") or LANGUAGE_NAMES.get(normalized) or normalized


def locale_payload(language_code: str | None) -> dict[str, Any]:
    normalized = normalize_language_code(language_code)
    fallback = load_locale(DEFAULT_LANGUAGE)
    current = load_locale(normalized)
    messages = {**fallback, **current}
    return {
        "language_code": normalized,
        "messages": messages,
        "languages": language_options(),
    }


def t(language_code: str | None, key: str, **params: Any) -> str:
    normalized = normalize_language_code(language_code)
    messages = locale_payload(normalized)["messages"]
    template = messages.get(key, key)
    if not params:
        return template
    return template.format(**params)


def parse_accept_language(header_value: str | None, fallback: str = DEFAULT_LANGUAGE) -> str:
    if not header_value:
        return fallback
    for part in header_value.split(","):
        token = part.split(";", maxsplit=1)[0].strip()
        normalized = normalize_language_code(token, fallback="")
        if normalized:
            return normalized
    return fallback


def save_generated_locale(
    language_code: str,
    native_name: str,
    messages: dict[str, str],
) -> str:
    normalized = _safe_language_code(language_code)
    if not normalized:
        raise ValueError("Invalid language code")
    payload = {
        "language.native_name": native_name or normalized,
        **messages,
    }
    GENERATED_LOCALES_DIR.mkdir(parents=True, exist_ok=True)
    path = GENERATED_LOCALES_DIR / f"{normalized}.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    load_locale.cache_clear()
    return normalized


def required_locale_keys() -> list[str]:
    return sorted(key for key in load_locale(DEFAULT_LANGUAGE).keys() if key != "language.native_name")
