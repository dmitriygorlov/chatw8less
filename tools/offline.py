"""Process-local isolation for demos/tests; never imported by the application."""
import logging
import importlib.util
import os
import socket
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def deny_network(*args, **kwargs):
    raise RuntimeError("Network access is disabled in the offline demo/test suite")


@contextmanager
def isolated_runtime():
    if any(name == "web_app" or name.startswith("bot.") for name in sys.modules):
        raise RuntimeError("Start offline checks in a fresh Python process")
    original_cwd = Path.cwd()
    original_connect = socket.socket.connect

    def local_connect(sock, address):
        # Windows asyncio may use a loopback socket pair for its internal wakeup.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        return deny_network()

    with TemporaryDirectory(prefix="chatw8less-offline-") as directory:
        root = Path(directory)
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
        env.update(OPENAI_API_KEY="offline-placeholder-not-a-key",
                   TELEGRAM_API_TOKEN="", RUN_TELEGRAM_IN_WEB="false",
                   DATABASE_PATH=str(root / "demo.sqlite3"),
                   GENERATED_LOCALES_DIR=str(root / "locales"),
                   SITE_URL="http://testserver")
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env, clear=True))
            stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
            stack.enter_context(patch("socket.socket.connect", local_connect))
            stack.enter_context(patch("httpx.HTTPTransport.handle_request", deny_network))
            stack.enter_context(patch("httpx.AsyncHTTPTransport.handle_async_request", deny_network))
            if importlib.util.find_spec("httpx2") is not None:
                stack.enter_context(patch("httpx2.HTTPTransport.handle_request", deny_network))
                stack.enter_context(patch("httpx2.AsyncHTTPTransport.handle_async_request", deny_network))
            os.chdir(root)
            try:
                from bot import config, i18n
                config.STORAGE_DIR = str(root / "storage")
                config.LOG_DIR = str(root / "logs")
                i18n.LEGACY_GENERATED_LOCALES_DIR = root / "storage" / "generated_locales"
                yield root
            finally:
                logging.shutdown()
                os.chdir(original_cwd)
