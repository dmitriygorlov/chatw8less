import logging
import os
import sys
from bot.config import LOG_DIR

os.makedirs(LOG_DIR, exist_ok=True)


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(os.path.join(LOG_DIR, "general.log"), encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
