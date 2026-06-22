"""Script to migrate old storage structure to year/month/day files."""

import json
import os

from bot.config import STORAGE_DIR


def migrate_user_file(path: str) -> None:
    """Convert single storage/{user_id}.json to new directory structure."""
    user_id = os.path.splitext(os.path.basename(path))[0]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    daily_limit = data.get("daily_limit")
    model_mode = data.get("model_mode")

    for date_str, day_data in data.items():
        if date_str == "daily_limit":
            continue
        year, month, day = date_str.split("-")
        dir_path = os.path.join(STORAGE_DIR, user_id, year, month)
        os.makedirs(dir_path, exist_ok=True)
        day_path = os.path.join(dir_path, f"{day}.json")
        with open(day_path, "w", encoding="utf-8") as df:
            json.dump(day_data, df, ensure_ascii=False, indent=2)

    meta_payload = {}
    if daily_limit is not None:
        meta_payload["daily_limit"] = daily_limit
    if model_mode is not None:
        meta_payload["model_mode"] = model_mode

    if meta_payload:
        meta_path = os.path.join(STORAGE_DIR, user_id, "meta.json")
        with open(meta_path, "w", encoding="utf-8") as mf:
            json.dump(meta_payload, mf, ensure_ascii=False, indent=2)

    # Backup old file
    backup_path = path + ".bak"
    os.replace(path, backup_path)


def main() -> None:
    if not os.path.isdir(STORAGE_DIR):
        print(f"No storage directory '{STORAGE_DIR}' found. Nothing to migrate.")
        return

    for fname in os.listdir(STORAGE_DIR):
        if not fname.endswith(".json"):
            continue
        full_path = os.path.join(STORAGE_DIR, fname)
        migrate_user_file(full_path)
        print(f"Migrated {fname}")


if __name__ == "__main__":
    main()
