from __future__ import annotations

import argparse
import sys

from .config import RuntimeConfig
from .sqlite_repository import SQLiteRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create the first GuardAId School Administrator. This command refuses "
            "to run after an active administrator already exists."
        )
    )
    parser.add_argument("--firebase-uid", required=True, help="Firebase Authentication user UID")
    parser.add_argument("--email", required=True, help="Authorised administrator email address")
    parser.add_argument("--display-name", required=True, help="Administrator display name")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = RuntimeConfig.from_env()
    if config.persistence_mode != "sqlite":
        print("Bootstrap requires GUARDAID_PERSISTENCE_MODE=sqlite.", file=sys.stderr)
        return 2
    repository = SQLiteRepository(config)
    try:
        profile = repository.bootstrap_first_administrator(
            args.firebase_uid.strip(),
            args.email.strip(),
            args.display_name.strip(),
        )
    except ValueError as exc:
        print(f"Bootstrap refused: {exc}", file=sys.stderr)
        return 1
    print(
        "First School Administrator activated: "
        f"{profile['display_name']} <{profile['email_cache']}>"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
