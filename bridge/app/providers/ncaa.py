"""stats.ncaa.org enrichment source (free; the official NCAA statistics site).

The only comprehensive index of NCAA athletes — ~3M player records across every sport, division,
and season back to the early '90s. Three steps: the DataTables JSON backend of /search/players →
candidate rows (name, player id, per-team career spans with org ids), then the player page → the
bio <dl> (class, position, height, hometown, high school), then the school's own athletic site →
the player's roster bio page (photo, plus any bio fields stats.ncaa.org lacked). The last hop is
deterministic because the career-cell org ids are the same NCAA org ids the member directory
(web3.ncaa.org/directory) keys on, and most athletic sites are Sidearm-hosted with the uniform
/sports/{slug}/roster/{season} → /roster/{name-slug}/{id} URL structure; non-Sidearm schools
simply skip.

The site sits behind Akamai, which rejects plain-requests/cloudscraper TLS fingerprints at the
edge (FlareSolverr doesn't apply — it solves Cloudflare, not Akamai), so the default client is
curl_cffi impersonating Chrome. Player pages additionally serve a JS-free proof-of-work
interstitial ("bm-verify"), solved inline by `_solve_interstitial`. See docs/ENRICHMENT.md §3.
"""

import re
import threading
import time
import unicodedata
from html import unescape
from typing import NamedTuple

from lxml import html

from bridge.app.providers.base import ProviderError
from bridge.app.providers.models import PerformerData

_BASE = "https://stats.ncaa.org"
_SEARCH_PAGE = f"{_BASE}/search/players"
_DATA_URL = f"{_BASE}/search/players/data"
_VERIFY_URL = f"{_BASE}/_sec/verify?provider=interstitial"
_DIRECTORY_URL = "https://web3.ncaa.org/directory/api/directory/memberList?type=12"
_MAX_CANDIDATES = 5  # eager-fetch the player page for the top matches; cached thereafter
_PAGE_SIZE = 100  # the backend honours more than the UI's 25; results come oldest-first

# '2019-20 - 2022-23 @<a href="/teams/history/WVB/796">Wisconsin Women's Volleyball</a>'
_SEG_HTML = re.compile(
    r'(\d{4})-\d{2} - (\d{4})-\d{2} @<a href="/teams/history/([A-Z0-9]+)/(\d+)"[^>]*>(.*?)</a>',
    re.S,
)
_PLAYER_LINK = re.compile(r"/players/(\d+)")
_HEIGHT = re.compile(r"^([4-7])-(\d{1,2})$")  # feet-inches; "0-0" means unknown
_BM_TOKEN = re.compile(r'"bm-verify":\s*"([^"]+)"')
_BM_POW = re.compile(r'var i = (\d+);\s*var j = i \+ Number\("(\d+)"\s*\+\s*"(\d+)"\)')
# The share-image meta tag carrying the player headshot: og:image (Sidearm, WMT — property=
# or name= depending on generation, content before or after) with twitter:image as fallback
# (WordPress sites like ukathletics.com omit og:image on bio pages).
def _share_image_re(kind: str) -> re.Pattern:
    return re.compile(
        rf'<meta[^>]*?(?:property|name)="{kind}"[^>]*?content="([^"]+)"'
        rf'|<meta[^>]*?content="([^"]+)"[^>]*?(?:property|name)="{kind}"'
    )


_SHARE_IMAGES = (_share_image_re("og:image"), _share_image_re("twitter:image"))
# Sites fall back to a stock share image when the player has no headshot — skip those.
_PLACEHOLDER_IMG = re.compile(r"default|placeholder|missing|/logos?/", re.I)
# The sports nav present on athletic-site pages — used to discover a site's actual sport slug
# when it differs from the default (e.g. Purdue's "volleyball", Ferris State's "wsoc").
# PrestoSports puts the season between slug and roster (/sports/wsoc/2025-26/roster); links
# may be site-absolute.
_NAV_ROSTER = re.compile(
    r'href="(?:https?://[^"/]+)?/sports/([a-z0-9-]+)(?:/\d{4}(?:-\d{2})?)?/roster/?"'
)

# Abbreviations sites use in sport slugs, keyed by the default slug's dehyphenated base.
_SLUG_ALIASES = {
    "basketball": {"bball", "bkb", "bb"},
    "soccer": {"soc"},
    "volleyball": {"vball", "vb", "volley"},
    "softball": {"sball", "sb"},
    "football": {"fball", "fb"},
    "baseball": {"base", "bsb"},
    "icehockey": {"hockey", "ice", "hky"},
    "fieldhockey": {"fh", "fhockey"},
    "lacrosse": {"lax", "lac"},
    "tennis": {"ten"},
    "swimminganddiving": {"swim", "swimdive"},
    "trackandfield": {"track", "trk"},
    "crosscountry": {"xc", "cross"},
    "gymnastics": {"gym"},
    "wrestling": {"wrest", "wrestle"},
    "rowing": {"row", "crew"},
    "beachvolleyball": {"bvb", "beach"},
}

_US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)

# stats.ncaa.org sport codes → Sidearm's default sport slugs. Slugs occasionally differ per
# site; a miss just skips the roster hop.
_SPORT_SLUGS = {
    "MFB": "football",
    "MBA": "baseball",
    "WSB": "softball",
    "MBB": "mens-basketball", "WBB": "womens-basketball",
    "MSO": "mens-soccer", "WSO": "womens-soccer",
    "MVB": "mens-volleyball", "WVB": "womens-volleyball",
    "WBV": "womens-beach-volleyball",
    "MIH": "mens-ice-hockey", "WIH": "womens-ice-hockey",
    "MLA": "mens-lacrosse", "WLA": "womens-lacrosse",
    "MTE": "mens-tennis", "WTE": "womens-tennis",
    "MGO": "mens-golf", "WGO": "womens-golf",
    "MSW": "mens-swimming-and-diving", "WSW": "womens-swimming-and-diving",
    "MXC": "mens-cross-country", "WXC": "womens-cross-country",
    "MTI": "mens-track-and-field", "MTO": "mens-track-and-field",
    "WTI": "womens-track-and-field", "WTO": "womens-track-and-field",
    "MWR": "wrestling", "WWR": "womens-wrestling",
    "WFH": "field-hockey",
    "MGY": "mens-gymnastics", "WGY": "womens-gymnastics",
    "WRO": "womens-rowing",
}

# Labels on a Sidearm roster-bio page, extracted from flattened text (works across both the
# classic and the nextgen page generations).
_BIO_STOP = (
    r"(?:Position|Class|Height|Hometown|High School|Major|Bio|Stats|Media|Jersey|Year|"
    r"Roster|Related)"
)


class _Segment(NamedTuple):
    start: int  # first season's starting calendar year
    end: int  # last season's ending calendar year ("2022-23" ends in 2023)
    team: str
    sport: str  # stats.ncaa.org sport code (WVB, MFB, …)
    org_id: str  # NCAA org id — same id space as the member directory


def _career_segments(raw_html: str) -> list[_Segment]:
    """Parse the career cell's HTML into segments, keeping each team's sport code + org id."""
    return [
        _Segment(
            int(m.group(1)),
            int(m.group(2)) + 1,
            unescape(re.sub(r"<[^>]+>", "", m.group(5))).strip(),
            m.group(3),
            m.group(4),
        )
        for m in _SEG_HTML.finditer(raw_html)
    ]


def _slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def _find_player_link(page: str, slug: str, name_slug: str) -> str | None:
    """The player's bio link on a roster page, matched by name slug (exact, then
    all-tokens-contained for middle names / suffixes).

    Three URL grammars: Sidearm's `roster/{name-slug}/{id}`, WMT Digital's
    `roster[/season/{season}]/player/{name-slug}`, PrestoSports' `{season}/bios/{slug}`.
    """
    esc = re.escape(slug)
    patterns = (
        re.compile(rf"/sports/{esc}/roster/([a-z][a-z0-9-]*)/\d+"),
        re.compile(rf"/sports/{esc}/roster/(?:season/[^/\"']+/)?player/([a-z][a-z0-9-]*)"),
        # PrestoSports: /sports/wsoc/2019-20/bios/irwin_morgan_g6mz (last_first + hash)
        re.compile(rf"/sports/{esc}/[^/\"']+/bios/([a-z][a-z0-9_]*)"),
    )
    links: dict[str, str] = {}
    for pattern in patterns:
        for m in pattern.finditer(page):
            if m.group(1) != "season":  # Sidearm pattern also matches roster/season/2019 nav
                links.setdefault(m.group(1), m.group(0))
    if name_slug in links:
        return links[name_slug]
    tokens = name_slug.split("-")
    for player_slug, link in links.items():
        if all(t in re.split(r"[-_]", player_slug) for t in tokens):
            return link
    return None


def _slug_matches(candidate: str, default_slug: str, gender: str) -> bool:
    """Whether a nav-discovered sport slug denotes the same sport+gender as our default.

    Handles full-word slugs ("volleyball" ~ "womens-volleyball", "track-field" ~
    "womens-track-and-field") and abbreviated ones ("wsoc", "wbball", "sball") via
    _SLUG_ALIASES. An unprefixed slug is accepted for either gender — schools drop the
    prefix when they sponsor only one; a gender prefix (full or single-letter) must match.
    """
    base = re.sub(r"^(mens|womens)-", "", default_slug).replace("and-", "").replace("-", "")
    aliases = {base} | _SLUG_ALIASES.get(base, set())

    def rest_ok(rest: str) -> bool:
        rest = rest.replace("-", "")
        return bool(rest) and any(
            rest == a or rest.startswith(a) or a.startswith(rest) for a in aliases
        )

    if candidate.startswith(("mens-", "womens-")):
        want = "M" if candidate.startswith("mens-") else "W"
        return want == gender and rest_ok(candidate.split("-", 1)[1])
    if rest_ok(candidate):  # unprefixed (single-gender program), e.g. "volleyball", "sball"
        return True
    if candidate[:1] in ("m", "w"):  # single-letter gender prefix, e.g. "wsoc", "mbball"
        return candidate[:1].upper() == gender and rest_ok(candidate[1:])
    return False


def _discover_slugs(page: str, default_slug: str, gender: str) -> list[str]:
    """Alternate slugs for our sport from the page's sports nav (any page carries it)."""
    return [
        cand
        for cand in dict.fromkeys(_NAV_ROSTER.findall(page))
        if cand != default_slug and _slug_matches(cand, default_slug, gender)
    ]


def _bio_field(text: str, label: str) -> str | None:
    m = re.search(rf"\b{label}\s*:?\s+(.{{1,60}}?)(?=\s+{_BIO_STOP}\b|$)", text)
    return m.group(1).strip(" :·|-") if m else None


def _gender(teams: list[str]) -> str | None:
    joined = " ".join(teams)
    if "Women's" in joined:
        return "Female"
    if "Men's" in joined or "Football" in joined or "Baseball" in joined:
        return "Male"
    return None


def _height_cm(value: str | None) -> str | None:
    if not value or not (m := _HEIGHT.match(value.strip())):
        return None
    return str(round((int(m.group(1)) * 12 + int(m.group(2))) * 2.54))


class NcaaProvider:
    id = "ncaa"
    label = "NCAA Stats"
    metered = False

    def __init__(self, client=None, min_interval: float = 0.5):
        # A requests-like session: .get/.post(url, ...) -> resp with .status_code/.text/.json().
        if client is None:
            from curl_cffi import requests as curl_requests

            client = curl_requests.Session(impersonate="chrome")
        self._client = client
        self._min_interval = min_interval
        self._last_ts = 0.0
        self._lock = threading.Lock()
        self._warmed = False
        self._directory: dict[str, str] | None = None  # org id -> athletic site host, lazy

    def _get(self, url: str, params: dict | None = None, xhr: bool = False):
        """Throttled GET; solves the Akamai proof-of-work interstitial once if served."""
        headers = {"Referer": _SEARCH_PAGE}
        if xhr:
            headers["X-Requested-With"] = "XMLHttpRequest"
        with self._lock:
            gap = self._min_interval - (time.monotonic() - self._last_ts)
            if gap > 0:
                time.sleep(gap)
            resp = self._client.get(url, params=params, headers=headers, timeout=30)
            if resp.status_code == 200 and "bm-verify" in resp.text:
                self._solve_interstitial(resp.text)
                resp = self._client.get(url, params=params, headers=headers, timeout=30)
            self._last_ts = time.monotonic()
        if resp.status_code == 403:
            raise ProviderError("ncaa: blocked by Akamai (TLS fingerprint rejected)")
        if resp.status_code == 200 and "bm-verify" in resp.text:
            raise ProviderError("ncaa: Akamai interstitial not cleared")
        return resp

    def _solve_interstitial(self, text: str) -> None:
        """The challenge is plain arithmetic: POST the bm-verify token + i + Number(hi + lo)."""
        token = _BM_TOKEN.search(text)
        pow_m = _BM_POW.search(text)
        if not (token and pow_m):
            raise ProviderError("ncaa: unrecognized Akamai challenge page")
        i, hi, lo = pow_m.groups()
        self._client.post(
            _VERIFY_URL,
            json={"bm-verify": token.group(1), "pow": int(i) + int(hi + lo)},
            headers={"Referer": _SEARCH_PAGE},
            timeout=30,
        )

    def search(self, term: str) -> list[PerformerData]:
        try:
            if not self._warmed:  # cookie-priming page load, once per session
                self._get(_SEARCH_PAGE, params={"q": term})
                self._warmed = True
            resp = self._get(
                _DATA_URL,
                params={
                    "sEcho": "1",
                    "iDisplayStart": "0",
                    "iDisplayLength": str(_PAGE_SIZE),
                    "sSearch": term,
                    "org_id_filter": "",
                    "sport_code_filter": "",
                },
                xhr=True,
            )
            resp.raise_for_status()
            rows = resp.json().get("aaData") or []
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001 - network/JSON failure
            raise ProviderError(f"ncaa: {e}") from e

        candidates = [c for c in (self._from_row(r) for r in rows) if c is not None]
        # The backend fuzzy-matches and returns oldest-first. Exact name matches outrank the
        # fuzzy ones ("Pukis" before "Pukish"); recency only breaks ties within each group.
        term_key = term.strip().lower()
        candidates.sort(
            key=lambda c: (
                c[0].name.strip().lower() == term_key,
                c[0].career_end or "",
                c[0].career_start or "",
            ),
            reverse=True,
        )

        out: list[PerformerData] = []
        for p, segments in candidates[:_MAX_CANDIDATES]:
            try:
                self._enhance(p)
            except Exception:  # noqa: BLE001 - keep the search-row candidate on a bad detail page
                pass
            try:
                self._roster_enhance(p, segments)
            except Exception:  # noqa: BLE001 - the roster hop is strictly best-effort
                pass
            out.append(p)
        return out

    def _from_row(self, row: dict) -> tuple[PerformerData, list[_Segment]] | None:
        """Map one DataTables row (name link + career cell) to a base candidate + its segments."""
        name_cell = html.fromstring(f"<div>{row.get('people-last_name', '')}</div>")
        link = name_cell.xpath(".//a/@href")
        pid = _PLAYER_LINK.search(link[0]) if link else None
        name = name_cell.text_content().strip()
        if not (pid and name):
            return None
        segments = _career_segments(row.get("players-career", ""))
        teams = list(dict.fromkeys(s.team for s in segments))
        p = PerformerData(
            source=self.id,
            source_entity_id=pid.group(1),
            name=name,
            gender=_gender(teams),
            urls=[f"{_BASE}/players/{pid.group(1)}"],
        )
        if segments:
            start, end = min(s.start for s in segments), max(s.end for s in segments)
            p.career_start, p.career_end = str(start), str(end)
            shown = " / ".join(teams[:3]) + (" …" if len(teams) > 3 else "")
            p.disambiguation = f"{shown}, {start}–{end}"
            # Player data outside the performer schema → custom_fields (provider-side only,
            # source-prefixed keys, flat scalar values).
            p.custom_fields["ncaa_teams"] = "; ".join(
                f"{s.team} ({s.start}–{s.end})" for s in segments
            )
        return p, segments

    def _enhance(self, p: PerformerData) -> None:
        """Fold the player page's bio <dl> (class/position/height/hometown/HS) into `p`."""
        resp = self._get(f"{_BASE}/players/{p.source_entity_id}")
        resp.raise_for_status()
        tree = html.fromstring(resp.text)
        bio: dict[str, str] = {}
        for dl in tree.xpath("//dl"):
            pairs = {
                dt.text_content().strip().rstrip(":"): dd.text_content().strip()
                for dt, dd in zip(dl.xpath("./dt"), dl.xpath("./dd"), strict=False)
            }
            if "Class" in pairs:
                bio = pairs
                break
        if not bio:
            return

        p.height = _height_cm(bio.get("Height")) or p.height
        hometown = bio.get("Hometown") or ""
        if hometown.rsplit(",", 1)[-1].strip() in _US_STATES:
            p.country = "US"

        for key, cf_key in (
            ("Position #", "ncaa_position"),
            ("Class", "ncaa_class"),
            ("Jersey #", "ncaa_jersey"),
            ("Hometown", "ncaa_hometown"),
            ("High School", "ncaa_high_school"),
        ):
            if bio.get(key):
                p.custom_fields[cf_key] = bio[key]

    # --- athletic-site roster hop (photo + bio gaps) ---

    def _fetch(self, url: str):
        """Throttled best-effort GET for non-stats.ncaa.org hosts (directory, athletic sites).

        Returns None on any transport failure — the roster hop never fails a search.
        """
        try:
            with self._lock:
                gap = self._min_interval - (time.monotonic() - self._last_ts)
                if gap > 0:
                    time.sleep(gap)
                resp = self._client.get(url, timeout=30)
                self._last_ts = time.monotonic()
            return resp
        except Exception:  # noqa: BLE001
            return None

    def _athletic_site(self, org_id: str) -> str | None:
        """The school's athletic-site base URL, from the NCAA member directory (fetched once).

        The directory keys on the same org ids as the stats.ncaa.org team links (verified:
        796 = Wisconsin → uwbadgers.com, 327 = Kansas St. → kstatesports.com).
        """
        if self._directory is None:
            resp = self._fetch(_DIRECTORY_URL)
            try:
                entries = resp.json() if resp is not None else []
            except Exception:  # noqa: BLE001
                entries = []
            self._directory = {
                str(e.get("orgId")): str(e.get("athleticWebUrl") or "").strip().rstrip("/")
                for e in entries
            }
        host = self._directory.get(str(org_id))
        if not host:
            return None
        return host if host.startswith("http") else f"https://{host}"

    def _roster_enhance(self, p: PerformerData, segments: list[_Segment]) -> None:
        """Follow the player's teams (most recent stint first) to an athletic-site roster and
        fold their bio page into the candidate: photo (og:image), bio URL, and any bio fields
        stats.ncaa.org lacked. Best-effort at every step — a school missing from the directory,
        a non-Sidearm site, or no roster/name match falls back to the previous team, and a
        total miss just leaves the candidate as-is.
        """
        name_slug = _slugify(p.name)
        for seg in reversed(segments):
            default_slug = _SPORT_SLUGS.get(seg.sport)
            site = self._athletic_site(seg.org_id)
            if not (default_slug and site):
                continue
            year = seg.end - 1  # the stint's last season's starting year
            # Fall sports use bare-year rosters (…/roster/2024), winter/spring split years.
            seasons = tuple(dict.fromkeys((str(year), f"{year}-{str(year + 1)[-2:]}")))
            gender = seg.sport[:1]
            link = None
            slugs = [default_slug]  # grows if the site's nav reveals a different slug
            home_mined = False
            i = 0
            while i < len(slugs) and not link:
                slug = slugs[i]
                i += 1
                for season in seasons:
                    # Sidearm's roster/{season}, WMT's roster/season/{season}, and
                    # PrestoSports' {season}/roster grammars.
                    for path in (
                        f"roster/{season}",
                        f"roster/season/{season}",
                        f"{season}/roster",
                    ):
                        resp = self._fetch(f"{site}/sports/{slug}/{path}")
                        if resp is None or getattr(resp, "status_code", 0) != 200:
                            continue
                        link = _find_player_link(resp.text, slug, name_slug)
                        if link:
                            break
                        for alt in _discover_slugs(resp.text, default_slug, gender):
                            if alt not in slugs:
                                slugs.append(alt)
                    if link:
                        break
                # Nothing matched and no alternates surfaced: mine the homepage nav once
                # (sites whose roster pages 404 on the default slug, e.g. PrestoSports).
                if i >= len(slugs) and not link and not home_mined:
                    home_mined = True
                    resp = self._fetch(f"{site}/")
                    if resp is not None and getattr(resp, "status_code", 0) == 200:
                        for alt in _discover_slugs(resp.text, default_slug, gender):
                            if alt not in slugs:
                                slugs.append(alt)
            if not link:
                continue
            bio_url = f"{site}{link}"
            resp = self._fetch(bio_url)
            if resp is not None and getattr(resp, "status_code", 0) == 200:
                self._apply_bio(p, resp.text, bio_url)
                return

    def _apply_bio(self, p: PerformerData, page: str, url: str) -> None:
        """Fold a Sidearm roster-bio page into the candidate; stats.ncaa.org data wins on
        conflict (only absent fields are filled)."""
        for pattern in _SHARE_IMAGES:
            m = pattern.search(page)
            if not m:
                continue
            img = unescape(m.group(1) or m.group(2))
            if not _PLACEHOLDER_IMG.search(img):
                if img not in p.images:
                    p.images.append(img)
                break
        if url not in p.urls:
            p.urls.append(url)
        text = re.sub(r"<(script|style).*?</\1>", "", page, flags=re.S)
        text = unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))
        for label, cf_key in (
            ("Position", "ncaa_position"),
            ("Class", "ncaa_class"),
            ("Hometown", "ncaa_hometown"),
            ("High School", "ncaa_high_school"),
            ("Major", "ncaa_major"),
        ):
            if not p.custom_fields.get(cf_key) and (val := _bio_field(text, label)):
                p.custom_fields[cf_key] = val
        if not p.height and (h := _bio_field(text, "Height")):
            p.height = _height_cm(h)
        hometown = str(p.custom_fields.get("ncaa_hometown") or "")
        if not p.country and hometown.rsplit(",", 1)[-1].strip() in _US_STATES:
            p.country = "US"
