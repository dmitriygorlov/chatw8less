# Security Notes

ChatW8Less is designed as a small private or family-use app, not as a multi-tenant SaaS.

Before deploying:

- do not commit `.env`, SQLite databases, `storage/`, `logs/`, or runtime check files;
- use a persistent volume for `storage/` in production;
- set `WEB_COOKIE_SECURE=true` when serving over HTTPS;
- restrict Telegram access with `ALLOWED_USER_IDS`;
- rotate `OPENAI_API_KEY` and `TELEGRAM_API_TOKEN` if they were ever exposed;
- review access and authentication before exposing the app beyond a small trusted group.

The web login uses passphrases stored as hashes, but this project does not currently include email auth, OAuth, RBAC, or rate limiting.
