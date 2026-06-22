import argparse

from bot.config import SITE_URL
from bot.db import create_or_update_user, list_users, set_user_passphrase
from bot.i18n import DEFAULT_LANGUAGE, normalize_language_code


def cmd_create(args):
    user = create_or_update_user(
        display_name=args.name,
        passphrase=args.phrase,
        telegram_user_id=args.telegram_id,
        user_id=args.user_id,
        language_code=args.language,
    )
    print(f"Created or updated user: {user['id']}")
    print(f"Display name: {user.get('display_name')}")
    if user.get("telegram_user_id"):
        print(f"Telegram user id: {user['telegram_user_id']}")
    print("Passphrase saved as hash.")
    print(f"Language: {user.get('language_code') or DEFAULT_LANGUAGE}")
    if SITE_URL:
        print(f"Site URL: {SITE_URL}")


def cmd_set_phrase(args):
    set_user_passphrase(args.user_id, args.phrase)
    print(f"Passphrase updated for user {args.user_id}")


def cmd_list(_args):
    users = list_users()
    if not users:
        print("No users found.")
        return
    for user in users:
        print(
            f"{user['id']} | name={user.get('display_name') or '-'} | "
            f"telegram={user.get('telegram_user_id') or '-'} | "
            f"language={user.get('language_code') or DEFAULT_LANGUAGE} | "
            f"web_phrase={'yes' if user.get('has_web_phrase') else 'no'}"
        )


def build_parser():
    parser = argparse.ArgumentParser(description="Manage ChatW8Less users")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Create or update a user")
    create_parser.add_argument("--name", required=True, help="Display name")
    create_parser.add_argument("--phrase", required=True, help="Passphrase for web login")
    create_parser.add_argument("--telegram-id", help="Telegram user id")
    create_parser.add_argument("--user-id", help="Explicit internal user id")
    create_parser.add_argument(
        "--language",
        default=DEFAULT_LANGUAGE,
        type=lambda value: normalize_language_code(value, fallback=DEFAULT_LANGUAGE),
        help="Initial language code: ru, en, or sr",
    )
    create_parser.set_defaults(func=cmd_create)

    list_parser = subparsers.add_parser("list", help="List users")
    list_parser.set_defaults(func=cmd_list)

    phrase_parser = subparsers.add_parser("set-phrase", help="Update user passphrase")
    phrase_parser.add_argument("--user-id", required=True, help="Internal user id")
    phrase_parser.add_argument("--phrase", required=True, help="New passphrase")
    phrase_parser.set_defaults(func=cmd_set_phrase)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
