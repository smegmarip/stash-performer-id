#!/usr/bin/env python3
"""Thin transport for the stash-performer-id image scraper.

Stash runs this for `imageByFragment`, piping the image fragment JSON on stdin. We forward it to
the metadata-provider service, which resolves the image's active name (Step 1) and returns a
ScrapedImage `{performers: [...]}`. We print that straight back to stdout for Stash.

Stdlib only (no pip deps) — Stash containers rarely have third-party packages installed.
Service URL: STASH_PERFORMER_ID_URL env var, default http://localhost:15000.
"""

import json
import os
import sys
import urllib.error
import urllib.request


def _resolve_base() -> str:
    """Service URL: env var, else a sibling `service_url` file, else localhost.

    The sibling file keeps the deployment-specific URL (e.g. host.docker.internal:15000 when
    Stash runs in a container) as data next to the scraper — no code edit per install.
    """
    env = os.environ.get("STASH_PERFORMER_ID_URL")
    if env:
        return env.rstrip("/")
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


BASE = _resolve_base()
TIMEOUT = float(os.environ.get("STASH_PERFORMER_ID_TIMEOUT", "15"))


def main() -> None:
    raw = sys.stdin.read()
    try:
        fragment = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        print(f"[stash-performer-id] bad fragment JSON: {e}", file=sys.stderr)
        print(json.dumps({"performers": []}))
        return

    req = urllib.request.Request(
        f"{BASE}/scrape/image",
        data=json.dumps(fragment).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError) as e:
        # Fail soft: log to stderr (Stash surfaces it), emit an empty result so the scrape
        # doesn't error out the tagger.
        print(f"[stash-performer-id] service unreachable at {BASE}: {e}", file=sys.stderr)
        print(json.dumps({"performers": []}))
        return

    # Pass the service's ScrapedImage through verbatim (already the right shape).
    sys.stdout.write(body)


if __name__ == "__main__":
    main()
