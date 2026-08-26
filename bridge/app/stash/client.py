"""Minimal Stash GraphQL client (httpx). API-key auth is optional (local instances often
run without auth). Used by the harvest to enumerate galleries/images.
"""

import json

import httpx


class StashError(RuntimeError):
    pass


class StashClient:
    def __init__(self, url: str, api_key: str | None = None, timeout: float = 30.0):
        self.endpoint = url.rstrip("/") + "/graphql"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key:
            headers["ApiKey"] = api_key
        self._client = httpx.Client(headers=headers, timeout=timeout)

    def query(self, query: str, variables: dict | None = None) -> dict:
        resp = self._client.post(
            self.endpoint, json={"query": query, "variables": variables or {}}
        )
        resp.raise_for_status()
        # Stash echoes on-disk file paths verbatim, so a Latin-1-named file (e.g. a 0xC9 "É"
        # byte) makes the JSON body invalid UTF-8 and strict resp.json() raises
        # UnicodeDecodeError, aborting the whole harvest. Decode leniently — the offending
        # byte becomes U+FFFD in that one path; everything else harvests normally.
        data = json.loads(resp.content.decode("utf-8", errors="replace"))
        if data.get("errors"):
            raise StashError(str(data["errors"]))
        return data["data"]

    def version(self) -> str:
        return self.query("{ version { version } }")["version"]["version"]

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "StashClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
