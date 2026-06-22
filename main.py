import asyncio

from bot.telegram_app import run_telegram_polling


if __name__ == "__main__":
    asyncio.run(run_telegram_polling())
