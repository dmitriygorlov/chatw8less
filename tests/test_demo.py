import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from bot import config

ROOT = Path(__file__).resolve().parents[1]


def test_offline_runtime_blocks_http_and_uses_temporary_storage():
    assert config.OPENAI_API_KEY == "offline-placeholder-not-a-key"
    assert not config.TELEGRAM_API_TOKEN
    assert Path(config.DATABASE_PATH).parent != ROOT / "storage"
    with pytest.raises(RuntimeError, match="Network access is disabled"):
        httpx.get("https://example.com")


def test_documented_demo_ignores_inherited_production_settings(tmp_path):
    forbidden_db = tmp_path / "must-not-be-created.sqlite3"
    env = dict(os.environ, DATABASE_PATH=str(forbidden_db),
               TELEGRAM_API_TOKEN="fictional-token", RUN_TELEGRAM_IN_WEB="true",
               MIGRATION_TARGET_URL="https://example.com", OPENAI_API_KEY="fictional-inherited-key")
    result = subprocess.run([sys.executable, "-m", "tools.demo"], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: offline demo; temporary data removed." in result.stdout
    assert '"real_api_calls": 0' in result.stdout
    assert not forbidden_db.exists()
