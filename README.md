# ChatW8Less

ChatW8Less is a Telegram bot + mobile-friendly web app for tracking food, calories, macros, and everyday nutrition questions with help from OpenAI models.

I started it for a very practical reason: I wanted to lose weight without turning every meal into a spreadsheet, and I also wanted to make food tracking easier for my mom. Telegram was the first obvious interface, because sending "70 g chicken, 20 g rice" is faster than opening a heavy calorie tracker. Later I added a web version because not everyone builds the habit of using Telegram, and sometimes a regular browser page is just easier.

This repository is the clean public version of a project that first lived as my private local tool. The private history contains experiments, personal configuration, and real usage data, so I rebuilt the public repo with a clean Git history and kept the useful product decisions, tests, deployment notes, and documentation.

The result is a personal nutrition assistant that can:

- estimate calories and macros from text;
- recognize food from photos;
- calculate nutrition per 100 g for recipes and mixed dishes;
- answer product, recipe, grocery, and nutrition questions with online search;
- keep a shared history between Telegram and the website;
- save meals and show daily / weekly / all-time nutrition stats;
- work in multiple UI languages, with generated locales for additional languages;
- give the web assistant a personal name per user, so the product feels less like a form and more like a small companion.

This is a pet project, but it is built as a real deployable app: FastAPI, aiogram, SQLite, Docker, Railway-friendly configuration, user sessions, and tests.

## Links

- Author: [Dmitriy Gorlov](https://github.com/dmitriygorlov)
- LinkedIn: [linkedin.com/in/ds-marketer](https://www.linkedin.com/in/ds-marketer)

## Features

### Food Tracking

- Text food parsing: `50 g chicken, 20 g rice, 10 g olive oil`.
- Photo food recognition with approximate weights.
- Manual save flow for Telegram and save confirmation on the website.
- Daily calorie limit and remaining calories.
- Saved meal history by day and meal number.
- Delete saved meals or individual items.

### Assistant Mode

The website has an "Ask assistant" mode for more natural questions. Each user can rename the assistant, and the UI uses that name in tabs, prompts, history, and assistant replies:

- "What can I cook from what I have at home?"
- "Is this product good for weight loss?"
- "Compare two products by calories and satiety."
- "Make a grocery list for low-calorie meals."

Online answers use the user's selected language and keep conversation context, so short follow-ups like "what about dumplings?" can continue the previous topic instead of starting from scratch.

### Website

- Passphrase login.
- Mobile-friendly dashboard.
- Shared message history with Telegram.
- Nutrition stats and saved meals.
- Separate tabs for calorie parsing, photo analysis, per-100-g estimates, and the assistant.
- Markdown rendering for assistant answers, including links, bold text, lists, and inline code.
- Language switcher.
- Model mode switcher.
- Per-user assistant name.

### Telegram

- `/start`, `/help`, `/stats`, `/limits`, `/edit`, `/100`, `/online`, `/model`, `/language`, `/site`.
- Inline buttons for saving, deleting, changing model mode, and changing language.
- Access is restricted by `ALLOWED_USER_IDS`.

### Localization

Built-in locales:

- Russian (`ru`)
- English (`en`)
- Serbian Latin (`sr`)

The app can also generate an additional locale through OpenAI and save it under `storage/generated_locales/`.

### Recent Product Iterations

The current public version includes the pieces that made the project feel more like a real app than a script:

- SQLite storage for users, sessions, messages, and saved meals.
- A web dashboard sharing the same history as Telegram.
- User-level language, model mode, daily limit, and assistant-name settings.
- A dedicated assistant context that does not mix casual assistant chat with calorie-parsing requests.
- A separate assistant model configuration (`GPT_MODEL_ASSISTANT`) and context-depth controls.
- Asset versioning and no-cache headers for smoother deploys.
- Smoke tests for database migrations, web API flows, Telegram behavior, locale generation, assistant context, and settings.

## Tech Stack

- Python 3.11
- FastAPI + Jinja templates for the website
- aiogram 3 for Telegram
- SQLite for users, sessions, messages, and nutrition history
- OpenAI API for text, image, online, and locale generation flows
- Docker / Docker Compose
- pytest smoke tests

## Project Structure

```text
bot/
  app_services.py      # application-level nutrition, online, locale, import flows
  chat_service.py      # shared chat/history flows for web and Telegram
  db.py                # SQLite schema, migrations, users, sessions, meals
  handlers.py          # Telegram command/message handlers
  i18n.py              # locale loading, fallback, generated locales
  openai_client.py     # OpenAI API wrappers
  telegram_app.py      # bot setup and command registration
locales/
  ru.json
  en.json
  sr.json
static/
  app.js
  styles.css
templates/
  index.html
tests/
  test_smoke.py
web_app.py             # FastAPI app and web API
manage_users.py        # CLI for creating/listing users
Dockerfile
docker-compose.yml
```

## Environment

Copy the example file:

```bash
cp .env.example .env
```

Required variables:

```env
OPENAI_API_KEY=...
TELEGRAM_API_TOKEN=...
ALLOWED_USER_IDS=123456789,987654321
SITE_URL=https://your-app.example.com
```

Useful production variables:

```env
DATABASE_PATH=/app/storage/chatw8less.sqlite3
WEB_COOKIE_SECURE=true
RUN_TELEGRAM_IN_WEB=true
SUPPORT_CONTACT=your_telegram_username
```

See [.env.example](.env.example) for the full list.

## Local Run

Install dependencies:

```bash
pip install -r requirements-dev.txt
```

Start the shared website + bot backend:

```bash
uvicorn web_app:app --host 127.0.0.1 --port 8000 --reload
```

Open:

```text
http://127.0.0.1:8000/
```

If `TELEGRAM_API_TOKEN` is configured and `RUN_TELEGRAM_IN_WEB=true`, Telegram polling starts in the same process.

Telegram-only run:

```bash
python main.py
```

## User Management

Create a Telegram-linked user:

```bash
python manage_users.py create --name "Family User" --telegram-id 123456789 --phrase "secret passphrase" --language ru
```

Create a web-only user:

```bash
python manage_users.py create --name "Guest" --phrase "another secret passphrase" --language en
```

List users:

```bash
python manage_users.py list
```

Change a passphrase:

```bash
python manage_users.py set-phrase --user-id 123456789 --phrase "new secret passphrase"
```

The app stores only passphrase hashes, not the original passphrases.

## Docker

Build and run:

```bash
docker compose up --build
```

The compose file mounts:

- `./storage` to `/app/storage`
- `./logs` to `/app/logs`

The app listens on `PORT` or `8000` by default.

## Railway Deployment Notes

This app works well as one Railway service:

1. Create a Railway service from the repository.
2. Add a persistent volume.
3. Set `DATABASE_PATH` to a path inside the mounted volume, for example `/app/storage/chatw8less.sqlite3`.
4. Set `SITE_URL` to the public Railway URL.
5. Set `WEB_COOKIE_SECURE=true`.
6. Set `RUN_TELEGRAM_IN_WEB=true` if the same service should run Telegram polling.
7. Add `OPENAI_API_KEY`, `TELEGRAM_API_TOKEN`, and `ALLOWED_USER_IDS`.

Do not store the SQLite database in the repository. Use a Railway volume or another persistent disk.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Current smoke tests cover:

- web login/session flow;
- shared message history;
- food analysis save flow;
- settings and language APIs;
- assistant name settings;
- generated locale flow;
- Telegram language behavior;
- online assistant context.

## Security Notes

- Never commit `.env`, SQLite databases, logs, or `storage/`.
- Keep `ALLOWED_USER_IDS` restricted for private/family use.
- Use `WEB_COOKIE_SECURE=true` behind HTTPS in production.
- Use a persistent volume for `storage/`.
- Rotate Telegram/OpenAI keys if they were ever committed or shared.

## Roadmap Ideas

- CSV/JSON export for saved nutrition history.
- Better onboarding for non-technical users.
- A "turn this assistant answer into a saved meal" flow.
- More robust admin tools.
- Optional reminders or gentle check-ins.

## License

MIT License. Feel free to use, fork, adapt, or build on top of it.
