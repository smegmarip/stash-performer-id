"""Local-run harness for the harvest (dev/QA). In production the harvest is triggered via
the service API. Usage:

    uv run python -m bridge.app.harvest.cli audit \
        --stash-url http://localhost:9999 --db ./cache/dev.sqlite
"""

import argparse
import json

from bridge.app.cache.db import Database
from bridge.app.config import get_settings
from bridge.app.harvest.galleries import harvest_galleries
from bridge.app.stash.client import StashClient


def cmd_audit(args: argparse.Namespace) -> None:
    settings = get_settings()
    db_path = args.db or settings.db_path
    stash_url = args.stash_url or settings.stash_url
    api_key = args.api_key or (
        settings.stash_api_key.get_secret_value() if settings.stash_api_key else None
    )

    db = Database(db_path)
    try:
        with StashClient(stash_url, api_key) as stash:
            print(f"Stash {stash.version()} @ {stash_url}")
            result = harvest_galleries(db, stash, per_page=args.per_page)
        summary = db.summary()
        print("\n=== harvest result ===")
        print(json.dumps(result, indent=2))
        print("\n=== audit summary ===")
        print(json.dumps(summary, indent=2))
        print("\n=== sample distinct names ===")
        for name in db.sample_names(args.sample):
            print(f"  {name}")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="harvest")
    sub = parser.add_subparsers(required=True)

    a = sub.add_parser("audit", help="Harvest galleries and print a candidate summary.")
    a.add_argument("--stash-url")
    a.add_argument("--api-key")
    a.add_argument("--db")
    a.add_argument("--per-page", type=int, default=100)
    a.add_argument("--sample", type=int, default=25)
    a.set_defaults(func=cmd_audit)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
