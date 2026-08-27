#!/usr/bin/env python3
"""Thin transport for the stash-performer-id image/scene scraper.

Stash runs this for `imageByFragment`/`sceneByFragment`, piping the fragment JSON on stdin. The
`--kind` arg (image|scene) selects the service endpoint. We forward the fragment to the
metadata-provider service, which resolves the entity's active name (Step 1) and returns a
ScrapedImage/ScrapedScene `{performers: [...]}`. We print that straight back to stdout for Stash.

The service URL is NOT hardcoded: it's discovered from the metadata provider the user registered
in Stash (Settings -> Metadata Providers -> Stash-Boxes), so Stash and the API can run on
different hosts. The transport asks Stash's own GraphQL for that stash-box's endpoint and derives
the API base from it. Env/sibling-file are kept only as explicit overrides / offline fallback.

Stdlib only (no pip deps) — Stash containers rarely have third-party packages installed.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

_KINDS = {"image", "scene"}

# Stash, reached from where the scraper runs (Stash invokes us in-process, so localhost).
STASH_URL = os.environ.get("STASH_URL", "http://localhost:9999").rstrip("/")
STASH_API_KEY = os.environ.get("STASH_API_KEY", "")
# Name of the registered Stash-Box for this provider (Settings -> Metadata Providers).
BOX_NAME = os.environ.get("STASH_PERFORMER_ID_BOX_NAME", "Stash Performer ID")


def _endpoint_to_base(endpoint: str) -> str:
    """A stash-box endpoint is the GraphQL URL; the HTTP API sits at its base."""
    base = endpoint.strip().rstrip("/")
    if base.endswith("/graphql"):
        base = base[: -len("/graphql")]
    return base


def _base_from_stash() -> str | None:
    """Ask Stash for the endpoint of our registered Stash-Box (matched by name)."""
    query = "{ configuration { general { stashBoxes { name endpoint } } } }"
    headers = {"Content-Type": "application/json"}
    if STASH_API_KEY:
        headers["ApiKey"] = STASH_API_KEY
    req = urllib.request.Request(
        f"{STASH_URL}/graphql",
        data=json.dumps({"query": query}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        boxes = data["data"]["configuration"]["general"]["stashBoxes"] or []
    except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
        print(f"[stash-performer-id] could not read Stash stash-box config: {e}", file=sys.stderr)
        return None
    for box in boxes:
        if (box.get("name") or "").strip() == BOX_NAME and box.get("endpoint"):
            return _endpoint_to_base(box["endpoint"])
    print(f"[stash-performer-id] no registered Stash-Box named {BOX_NAME!r}", file=sys.stderr)
    return None


def _resolve_base() -> str:
    """Prefer an explicit env override, then the endpoint registered in Stash, then a sibling
    `service_url` file, then localhost."""
    env = os.environ.get("STASH_PERFORMER_ID_URL")
    if env:
        return env.rstrip("/")
    from_stash = _base_from_stash()
    if from_stash:
        return from_stash
    sibling = os.path.join(os.path.dirname(os.path.abspath(__file__)), "service_url")
    try:
        with open(sibling, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line.rstrip("/")
    except OSError:
        pass
    return "http://localhost:15000"


def main() -> None:
    parser = argparse.ArgumentParser(prog="stash-performer-id-scrape")
    parser.add_argument("--kind", choices=sorted(_KINDS), default="image")
    args = parser.parse_args()

    base = _resolve_base()  # resolved per invocation (lazy — keeps import side-effect-free)
    timeout = float(os.environ.get("STASH_PERFORMER_ID_TIMEOUT", "15"))
    raw = sys.stdin.read()
    try:
        fragment = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        print(f"[stash-performer-id] bad fragment JSON: {e}", file=sys.stderr)
        print(json.dumps({"performers": []}))
        return

    req = urllib.request.Request(
        f"{base}/scrape/{args.kind}",
        data=json.dumps(fragment).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError) as e:
        # Fail soft: log to stderr (Stash surfaces it), emit an empty result so the scrape
        # doesn't error out the tagger.
        print(f"[stash-performer-id] service unreachable at {base}: {e}", file=sys.stderr)
        print(json.dumps({"performers": []}))
        return

    # Pass the service's ScrapedImage/ScrapedScene through verbatim (already the right shape).
    sys.stdout.write(body)


if __name__ == "__main__":
    main()
