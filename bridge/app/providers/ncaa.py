"""stats.ncaa.org enrichment source (free; the official NCAA statistics site).

The only comprehensive index of NCAA athletes — ~3M player records across every sport, division,
and season back to the early '90s. Two steps: the DataTables JSON backend of /search/players →
candidate rows (name, player id, per-team career spans), then the player page → the bio <dl>
(class, position, height, hometown, high school). Bios are thin by design — no birthdate or
photos; team-website roster scraping is a planned follow-up source.

The site sits behind Akamai, which rejects plain-requests/cloudscraper TLS fingerprints at the
edge (FlareSolverr doesn't apply — it solves Cloudflare, not Akamai), so the default client is
curl_cffi impersonating Chrome. Player pages additionally serve a JS-free proof-of-work
interstitial ("bm-verify"), solved inline by `_solve_interstitial`. See docs/ENRICHMENT.md §3.
"""

import re
import threading
import time

from lxml import html

from bridge.app.providers.base import ProviderError
from bridge.app.providers.models import PerformerData

_BASE = "https://stats.ncaa.org"
_SEARCH_PAGE = f"{_BASE}/search/players"
_DATA_URL = f"{_BASE}/search/players/data"
_VERIFY_URL = f"{_BASE}/_sec/verify?provider=interstitial"
_MAX_CANDIDATES = 5  # eager-fetch the player page for the top matches; cached thereafter
_PAGE_SIZE = 100  # the backend honours more than the UI's 25; results come oldest-first

# "2019-20 - 2022-23 @Wisconsin Women's Volleyball" (segments run together in the career cell)
_SEG = re.compile(r"(\d{4})-\d{2} - (\d{4})-\d{2} @")
_PLAYER_LINK = re.compile(r"/players/(\d+)")
_HEIGHT = re.compile(r"^([4-7])-(\d{1,2})$")  # feet-inches; "0-0" means unknown
_BM_TOKEN = re.compile(r'"bm-verify":\s*"([^"]+)"')
_BM_POW = re.compile(r'var i = (\d+);\s*var j = i \+ Number\("(\d+)"\s*\+\s*"(\d+)"\)')

_US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)


def _career_segments(text: str) -> list[tuple[int, int, str]]:
    """Parse the career cell text into (start_year, end_year, team) segments."""
    matches = list(_SEG.finditer(text))
    out: list[tuple[int, int, str]] = []
    for i, m in enumerate(matches):
        stop = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        team = text[m.end() : stop].strip()
        # "2022-23" ends in calendar year 2023
        out.append((int(m.group(1)), int(m.group(2)) + 1, team))
    return out


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
        # The backend returns matches oldest-first; recent players are the likelier matches here.
        candidates.sort(key=lambda p: (p.career_end or "", p.career_start or ""), reverse=True)

        out: list[PerformerData] = []
        for p in candidates[:_MAX_CANDIDATES]:
            try:
                self._enhance(p)
            except Exception:  # noqa: BLE001 - keep the search-row candidate on a bad detail page
                pass
            out.append(p)
        return out

    def _from_row(self, row: dict) -> PerformerData | None:
        """Map one DataTables row (name link + career cell) to a base candidate."""
        name_cell = html.fromstring(f"<div>{row.get('people-last_name', '')}</div>")
        link = name_cell.xpath(".//a/@href")
        pid = _PLAYER_LINK.search(link[0]) if link else None
        name = name_cell.text_content().strip()
        if not (pid and name):
            return None
        segments = _career_segments(
            html.fromstring(f"<div>{row.get('players-career', '')}</div>").text_content()
        )
        teams = list(dict.fromkeys(team for _, _, team in segments))
        p = PerformerData(
            source=self.id,
            source_entity_id=pid.group(1),
            name=name,
            gender=_gender(teams),
            urls=[f"{_BASE}/players/{pid.group(1)}"],
        )
        if segments:
            start, end = min(s for s, _, _ in segments), max(e for _, e, _ in segments)
            p.career_start, p.career_end = str(start), str(end)
            shown = " / ".join(teams[:3]) + (" …" if len(teams) > 3 else "")
            p.disambiguation = f"{shown}, {start}–{end}"
            # Player data outside the performer schema → custom_fields (provider-side only,
            # source-prefixed keys, flat scalar values).
            p.custom_fields["ncaa_teams"] = "; ".join(
                f"{team} ({s}–{e})" for s, e, team in segments
            )
        return p

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
