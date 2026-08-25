"""Babepedia enrichment source (free; HTML scrape behind Cloudflare).

The richest bio source for this domain — the full performer superset. Two steps: ajax-search →
candidate slugs, then the babe page → the bio via lxml XPath. The extraction (selectors,
sanitizers, URL/image handling) is ported from Stash's CommunityScrapers `Babepedia.py`; images
are kept as URLs (served via the /images proxy) rather than base64. Cloudflare is handled with
cloudscraper. See docs/ENRICHMENT.md §3.
"""

import json
import re
from datetime import datetime
from urllib.parse import urlencode

import requests
from lxml import html

from bridge.app.providers.base import ProviderError
from bridge.app.providers.models import PerformerData

_BASE = "https://www.babepedia.com"
_MAX_CANDIDATES = 5  # eager-fetch detail for the top matches; cached thereafter
_BLOCKED = {403, 429, 503}  # Cloudflare challenge → fall back to FlareSolverr


class _FSResp:
    """A minimal response wrapper around a FlareSolverr solution."""

    def __init__(self, text: str, status: int):
        self.text = text
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise ProviderError(f"babepedia (flaresolverr): HTTP {self.status_code}")

    def json(self):
        # FlareSolverr renders in Chrome, so a JSON endpoint comes back HTML-wrapped (<pre>…</pre>).
        try:
            return json.loads(self.text.strip())
        except json.JSONDecodeError:
            tree = html.fromstring(self.text)
            pre = tree.xpath("//pre/text()")
            return json.loads(pre[0] if pre else tree.text_content())


def _bio(tree, label: str, selector: str = "") -> str | None:
    el = tree.xpath(
        f'//span[contains(text(), "{label}")]/following-sibling::span{selector}/text()'
    )
    return el[0].strip() if el else None


def _hair(value: str) -> str:
    return "Brunette" if value.lower() == "brown" else value


class BabepediaProvider:
    id = "babepedia"
    label = "Babepedia"
    metered = False

    def __init__(self, client=None, flaresolverr_url: str | None = None):
        # A requests-like client: .get(url, params=?) -> resp with .text/.json()/.raise_for_status.
        if client is None:
            import cloudscraper

            client = cloudscraper.create_scraper()
        self._client = client
        self._flaresolverr = flaresolverr_url

    def _get(self, url: str, params: dict | None = None):
        """cloudscraper first; on a Cloudflare block, fall back to FlareSolverr (if configured)."""
        blocked = False
        try:
            resp = self._client.get(url, params=params)
            if getattr(resp, "status_code", 200) not in _BLOCKED:
                return resp
            blocked = True
        except Exception:  # noqa: BLE001 - network/cloudflare failure -> try FlareSolverr
            blocked = True
        if blocked and self._flaresolverr:
            full = url + ("?" + urlencode(params) if params else "")
            return self._flaresolverr_get(full)
        raise ProviderError("babepedia: blocked by Cloudflare (no FlareSolverr configured)")

    def _flaresolverr_get(self, url: str) -> _FSResp:
        try:
            r = requests.post(
                self._flaresolverr, json={"cmd": "request.get", "url": url}, timeout=70
            )
            r.raise_for_status()
            sol = r.json().get("solution") or {}
        except requests.RequestException as e:
            raise ProviderError(f"babepedia (flaresolverr): {e}") from e
        return _FSResp(sol.get("response", ""), sol.get("status", 200))

    def search(self, term: str) -> list[PerformerData]:
        try:
            resp = self._get(f"{_BASE}/ajax-search.php", params={"term": term.replace("-", " ")})
            resp.raise_for_status()
            results = resp.json()
        except ProviderError:
            raise
        except Exception as e:  # cloudscraper/requests raise plain exceptions
            raise ProviderError(f"babepedia: {e}") from e

        slugs: list[str] = []
        for r in results:
            slug = str(r.get("value", "")).replace(" ", "_")
            if slug and slug not in slugs:
                slugs.append(slug)

        out: list[PerformerData] = []
        for slug in slugs[:_MAX_CANDIDATES]:
            try:
                out.append(self._detail(slug))
            except Exception:  # noqa: BLE001 - skip a bad profile, keep the rest
                continue
        return out

    def _detail(self, slug: str) -> PerformerData:
        url = f"{_BASE}/babe/{slug}"
        resp = self._get(url)
        resp.raise_for_status()
        tree = html.fromstring(resp.text)

        p = PerformerData(
            source=self.id, source_entity_id=slug, name=slug.replace("_", " "), gender="Female"
        )

        h1 = tree.xpath('//h1[@id="babename"]')
        if h1:
            p.name = h1[0].text.strip()
        p.urls = [url]

        aka = tree.xpath('//h2[@id="aka"][1]/text()')
        if aka:
            p.aliases = [a.strip() for a in aka[0].strip().split(" - ") if a.strip()]

        p.birthdate = _birthdate(tree)
        death = _bio(tree, "Died")
        if death:
            m = re.sub(r"\w+ (\d+)(?:st|nd|rd|th) of (\w+) (\d+) \(.+\)", r"\1 \2 \3", death)
            try:
                p.death_date = datetime.strptime(m, "%d %B %Y").date().isoformat()
            except ValueError:
                pass

        career = _bio(tree, "Years active")
        if career and "-" in career.split(" ", 1)[0]:
            start, end = career.split(" ", 1)[0].split("-", 1)
            p.career_start = start or None
            p.career_end = None if end in ("Present", "") else end

        flags = tree.xpath(
            '//span[contains(text(), "Nationality")]/following-sibling::span'
            '//span[contains(@class, "fi-")]/@class'
        )
        if flags:
            m = re.search(r"fi fi-([a-z]{2})", flags[0])
            if m:
                p.country = m.group(1).upper()

        p.ethnicity = _bio(tree, "Ethnicity", "/a")
        eye = _bio(tree, "Eye color", "/a")
        if eye:
            p.eye_color = eye
        hair = _bio(tree, "Hair color", "/a")
        if hair:
            p.hair_color = _hair(hair)

        height = _bio(tree, "Height")
        if height and (m := re.search(r"(\d+) cm", height)):
            p.height = m.group(1)
        weight = _bio(tree, "Weight")
        if weight and (m := re.search(r"(\d+) kg", weight)):
            p.weight = m.group(1)

        p.measurements = _measurements(tree)
        boobs = _bio(tree, "Boobs", "/a")
        if boobs:
            p.fake_tits = "Natural" if "Real" in boobs else "Fake" if "Fake" in boobs else None

        for label in ("Tattoos", "Piercings"):
            val = _bio(tree, label)
            if val and val != "None":
                setattr(p, label.lower(), val)

        bio = tree.xpath('//p[@id="biotext"]')
        if bio:
            p.details = bio[0].text_content().strip() or None

        p.urls += _social_urls(tree)
        p.images = _images(tree)
        return p


def _birthdate(tree) -> str | None:
    born = tree.xpath('//span[contains(text(), "Born:")]/following-sibling::span/a')
    if not born:
        return None
    if len(born) >= 2:
        txt = " ".join(a.text_content().strip() for a in born[:2])
        clean = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", txt)
        try:
            return datetime.strptime(clean, "%d of %B %Y").date().isoformat()
        except ValueError:
            return None
    return born[0].text_content().strip() or None  # year only


def _measurements(tree) -> str | None:
    m = _bio(tree, "Measurements")
    if m:
        # drop the "(.. cm)" metric conversion; normalize en/em dashes to "-"
        m = m.split("(")[0].strip().replace("–", "-").replace("—", "-")
    cup = _bio(tree, "Bra/cup size")
    if m and cup:
        parts = m.split("-")
        if len(parts) == 3:
            return f"{cup}-{parts[1]}-{parts[2]}"
    return m


def _social_urls(tree) -> list[str]:
    proxy = {"https://www.babepedia.com/onlyfans/": "https://onlyfans.com/", "/onlyfans/": "https://onlyfans.com/"}
    out: list[str] = []
    for href in tree.xpath('//div[@id="socialicons"]/a/@href'):
        for prefix, repl in proxy.items():
            if href.startswith(prefix):
                out.append(href.replace(prefix, repl, 1))
                break
        else:
            if href.startswith("http"):
                out.append(href)
    return out


def _images(tree) -> list[str]:
    hrefs = tree.xpath('//div[@id="profbox2"]//a[@class="img"]/@href') + tree.xpath(
        '//div[contains(@class,"useruploads2")]//a[@class="img"]/@href'
    )
    return [h if h.startswith("http") else f"{_BASE}{h}" for h in hrefs]
