import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.config import DATABASE_PATH, DEFAULT_MODEL_MODE, WEB_SESSION_DAYS
from bot.i18n import DEFAULT_LANGUAGE, EXISTING_USER_DEFAULT_LANGUAGE, normalize_language_code

DEFAULT_ASSISTANT_NAME = "Alex"
MAX_ASSISTANT_NAME_LENGTH = 40


DB_LOCK = threading.RLock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_str() -> str:
    return _utcnow().isoformat()


def _ensure_db_dir() -> None:
    Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_connection():
    _ensure_db_dir()
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                telegram_user_id TEXT UNIQUE,
                passphrase_salt TEXT,
                passphrase_hash TEXT,
                daily_limit INTEGER,
                model_mode TEXT,
                assistant_name TEXT NOT NULL DEFAULT 'Alex',
                language_code TEXT NOT NULL DEFAULT 'ru',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                source TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS web_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS nutrition_days (
                user_id TEXT NOT NULL,
                date_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (user_id, date_key),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_user_created_at
                ON messages(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_sessions_expires_at
                ON web_sessions(expires_at);
            """
        )
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "language_code" not in columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN language_code TEXT NOT NULL DEFAULT 'ru'"
            )
            conn.execute(
                "UPDATE users SET language_code = ? WHERE language_code IS NULL OR language_code = ''",
                (EXISTING_USER_DEFAULT_LANGUAGE,),
            )
        if "assistant_name" not in columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN assistant_name TEXT NOT NULL DEFAULT 'Alex'"
            )
            conn.execute(
                "UPDATE users SET assistant_name = ? WHERE assistant_name IS NULL OR assistant_name = ''",
                (DEFAULT_ASSISTANT_NAME,),
            )


def _row_to_dict(row):
    return dict(row) if row is not None else None


def _hash_secret(secret: str, salt_b64: str | None = None) -> tuple[str, str]:
    salt = base64.b64decode(salt_b64) if salt_b64 else secrets.token_bytes(16)
    digest = hashlib.scrypt(
        secret.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return (
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_secret(secret: str, salt_b64: str, hash_b64: str) -> bool:
    _, digest_b64 = _hash_secret(secret, salt_b64=salt_b64)
    return hmac.compare_digest(digest_b64, hash_b64)


def _hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_user(
    user_id: str,
    telegram_user_id: str | None = None,
    display_name: str | None = None,
    language_code: str | None = None,
) -> dict:
    init_db()
    now = _utcnow_str()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            normalized_language = normalize_language_code(
                language_code,
                fallback=DEFAULT_LANGUAGE if language_code is not None else EXISTING_USER_DEFAULT_LANGUAGE,
            )
            conn.execute(
                """
                INSERT INTO users (
                    id, display_name, telegram_user_id, daily_limit, model_mode, language_code, created_at, updated_at
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    display_name,
                    telegram_user_id,
                    DEFAULT_MODEL_MODE,
                    normalized_language,
                    now,
                    now,
                ),
            )
        else:
            next_display_name = display_name or row["display_name"]
            next_telegram_id = telegram_user_id or row["telegram_user_id"]
            conn.execute(
                """
                UPDATE users
                SET display_name = ?, telegram_user_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_display_name, next_telegram_id, now, user_id),
            )

        ensure_primary_conversation(user_id, conn=conn)
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_dict(updated)


def get_or_create_user_for_telegram(
    telegram_user_id: int | str,
    display_name: str | None = None,
    language_code: str | None = None,
) -> dict:
    normalized_telegram_id = str(telegram_user_id)
    return ensure_user(
        user_id=normalized_telegram_id,
        telegram_user_id=normalized_telegram_id,
        display_name=display_name,
        language_code=language_code,
    )


def get_user(user_id: str) -> dict | None:
    init_db()
    with get_connection() as conn:
        return _row_to_dict(
            conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        )


def list_users() -> list[dict]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, display_name, telegram_user_id,
                   passphrase_hash IS NOT NULL AS has_web_phrase,
                   language_code,
                   created_at, updated_at
            FROM users
            ORDER BY created_at
            """
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def create_or_update_user(
    display_name: str,
    passphrase: str,
    telegram_user_id: int | str | None = None,
    user_id: str | None = None,
    language_code: str | None = None,
) -> dict:
    init_db()
    normalized_telegram_id = str(telegram_user_id) if telegram_user_id is not None else None
    normalized_user_id = user_id or normalized_telegram_id or f"web_{secrets.token_hex(6)}"
    user = ensure_user(
        user_id=normalized_user_id,
        telegram_user_id=normalized_telegram_id,
        display_name=display_name,
        language_code=language_code,
    )
    set_user_passphrase(normalized_user_id, passphrase)
    return get_user(normalized_user_id) or user


def set_user_passphrase(user_id: str, passphrase: str) -> None:
    init_db()
    ensure_user(user_id)
    salt_b64, hash_b64 = _hash_secret(passphrase)
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE users
            SET passphrase_salt = ?, passphrase_hash = ?, updated_at = ?
            WHERE id = ?
            """,
            (salt_b64, hash_b64, _utcnow_str(), user_id),
        )


def get_user_language(user_id: str) -> str:
    user = get_user(user_id)
    return normalize_language_code(
        user.get("language_code") if user else None,
        fallback=DEFAULT_LANGUAGE,
    )


def normalize_assistant_name(value: str | None) -> str:
    name = (value or "").strip()
    if not name:
        raise ValueError("Assistant name is required")
    if len(name) > MAX_ASSISTANT_NAME_LENGTH:
        raise ValueError(f"Assistant name must be {MAX_ASSISTANT_NAME_LENGTH} characters or less")
    return name


def get_user_assistant_name(user_id: str) -> str:
    user = get_user(user_id)
    return (user or {}).get("assistant_name") or DEFAULT_ASSISTANT_NAME


def set_user_assistant_name(user_id: str, assistant_name: str) -> str:
    normalized = normalize_assistant_name(assistant_name)
    init_db()
    ensure_user(user_id)
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE users
            SET assistant_name = ?, updated_at = ?
            WHERE id = ?
            """,
            (normalized, _utcnow_str(), user_id),
        )
    return normalized


def set_user_language(user_id: str, language_code: str) -> str:
    normalized = normalize_language_code(language_code, fallback="")
    if not normalized:
        raise ValueError(f"Unsupported language: {language_code}")
    init_db()
    ensure_user(user_id, language_code=normalized)
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE users
            SET language_code = ?, updated_at = ?
            WHERE id = ?
            """,
            (normalized, _utcnow_str(), user_id),
        )
    return normalized


def user_has_passphrase(user_id: str) -> bool:
    user = get_user(user_id)
    return bool(user and user.get("passphrase_hash"))


def get_user_by_passphrase(passphrase: str) -> dict | None:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM users
            WHERE passphrase_hash IS NOT NULL AND passphrase_salt IS NOT NULL
            """
        ).fetchall()
    for row in rows:
        if verify_secret(passphrase, row["passphrase_salt"], row["passphrase_hash"]):
            return _row_to_dict(row)
    return None


def ensure_primary_conversation(user_id: str, conn: sqlite3.Connection | None = None) -> str:
    conversation_id = f"primary:{user_id}"
    if conn is not None:
        existing = conn.execute(
            "SELECT id FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if existing is None:
            now = _utcnow_str()
            conn.execute(
                """
                INSERT INTO conversations (id, user_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, user_id, "Primary conversation", now, now),
            )
        return conversation_id

    with get_connection() as connection:
        return ensure_primary_conversation(user_id, conn=connection)


def add_message(user_id: str, role: str, content: str, source: str) -> None:
    init_db()
    ensure_user(user_id)
    conversation_id = ensure_primary_conversation(user_id)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO messages (user_id, conversation_id, role, source, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, conversation_id, role, source, content, _utcnow_str()),
        )


def get_messages(user_id: str, limit: int = 200, source: str | None = None) -> list[dict]:
    init_db()
    conversation_id = ensure_primary_conversation(user_id)
    with get_connection() as conn:
        if source:
            rows = conn.execute(
                """
                SELECT id, role, source, content, created_at
                FROM messages
                WHERE conversation_id = ? AND source = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (conversation_id, source, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, role, source, content, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
    return [_row_to_dict(row) for row in reversed(rows)]


def get_recent_context(
    user_id: str,
    limit: int = 12,
    source: str | None = None,
    exclude_source: str | None = None,
) -> list[dict]:
    if not exclude_source:
        return get_messages(user_id, limit=limit, source=source)

    init_db()
    conversation_id = ensure_primary_conversation(user_id)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, role, source, content, created_at
            FROM messages
            WHERE conversation_id = ? AND source != ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (conversation_id, exclude_source, limit),
        ).fetchall()
    return [_row_to_dict(row) for row in reversed(rows)]


def create_web_session(user_id: str) -> str:
    init_db()
    raw_token = secrets.token_urlsafe(32)
    session_id = f"sess_{secrets.token_hex(8)}"
    now = _utcnow()
    expires_at = now + timedelta(days=WEB_SESSION_DAYS)

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO web_sessions (id, user_id, token_hash, created_at, expires_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                user_id,
                _hash_session_token(raw_token),
                now.isoformat(),
                expires_at.isoformat(),
                now.isoformat(),
            ),
        )
    return raw_token


def get_user_by_session_token(raw_token: str) -> dict | None:
    init_db()
    hashed = _hash_session_token(raw_token)
    now_str = _utcnow_str()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT u.*
            FROM web_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ?
            """,
            (hashed, now_str),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE web_sessions SET last_seen_at = ? WHERE token_hash = ?",
            (now_str, hashed),
        )
        conn.execute("DELETE FROM web_sessions WHERE expires_at <= ?", (now_str,))
        return _row_to_dict(row)


def delete_web_session(raw_token: str) -> None:
    init_db()
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM web_sessions WHERE token_hash = ?",
            (_hash_session_token(raw_token),),
        )


def load_user_state(user_id: str) -> dict:
    init_db()
    user = get_user(user_id)
    if user is None:
        return {}

    data: dict = {}
    if user.get("daily_limit") is not None:
        data["daily_limit"] = user["daily_limit"]
    if user.get("model_mode"):
        data["model_mode"] = user["model_mode"]

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT date_key, payload_json
            FROM nutrition_days
            WHERE user_id = ?
            ORDER BY date_key
            """,
            (user_id,),
        ).fetchall()

    for row in rows:
        data[row["date_key"]] = json.loads(row["payload_json"])
    return data


def save_user_state(user_id: str, data: dict) -> None:
    init_db()
    ensure_user(user_id)
    meta_daily_limit = data.get("daily_limit")
    meta_model_mode = data.get("model_mode", DEFAULT_MODEL_MODE)
    day_items = {
        key: value
        for key, value in data.items()
        if key not in {"daily_limit", "model_mode"} and value
    }

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE users
            SET daily_limit = ?, model_mode = ?, updated_at = ?
            WHERE id = ?
            """,
            (meta_daily_limit, meta_model_mode, _utcnow_str(), user_id),
        )
        conn.execute("DELETE FROM nutrition_days WHERE user_id = ?", (user_id,))
        for date_key, payload in day_items.items():
            conn.execute(
                """
                INSERT INTO nutrition_days (user_id, date_key, payload_json)
                VALUES (?, ?, ?)
                """,
                (user_id, date_key, json.dumps(payload, ensure_ascii=False)),
            )
