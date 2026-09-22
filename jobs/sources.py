"""Job board fetchers. Standard library only; no scraping, no browser automation.

Every source here is a public API, official JSON board endpoint, or RSS feed. LinkedIn
and Indeed are deliberately absent: both block automated access and ban accounts for it,
and a LinkedIn profile is a career asset worth more than the extra listings.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.client import IncompleteRead

UA = "Mozilla/5.0 (compatible; jobtrack/1.0; personal job search)"
TIMEOUT = 30


@dataclass
class Posting:
    """One normalised job posting, before scoring."""

    source: str
    company: str
    title: str
    url: str
    location: str = ""
    description: str = ""
    posted_at: str = ""
    tags: list[str] = field(default_factory=list)
    visa_sponsorship: bool = False
    # Where the employer will hire, when a feed states it as data rather than prose
    # (Himalayas' locationRestrictions). Empty means not stated, NOT unrestricted.
    countries: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Dedupe key - same role posted to several boards collapses to one."""
        norm = lambda s: re.sub(r"[^a-z0-9]+", "", s.lower())  # noqa: E731
        return f"{norm(self.company)}::{norm(self.title)}"

    @property
    def haystack(self) -> str:
        return " ".join([self.title, self.location, self.description, " ".join(self.tags)]).lower()


def _inflate(raw: bytes, encoding: str) -> bytes:
    """Decode a response body per its Content-Encoding, tolerating a truncated stream.

    Decompresses incrementally rather than with `gzip.decompress`, which raises on a
    partial stream and would throw away a whole fetch that `_repair` could still salvage.
    Anything unrecognised is passed through untouched.
    """
    enc = encoding.strip().lower()
    if enc not in ("gzip", "x-gzip", "deflate"):
        return raw
    wbits = zlib.MAX_WBITS | 16 if enc in ("gzip", "x-gzip") else zlib.MAX_WBITS
    for bits in (wbits, -zlib.MAX_WBITS):  # some servers send deflate with no zlib header
        try:
            out = zlib.decompressobj(bits).decompress(raw)
        except zlib.error:
            continue
        if out:
            return out
    return raw


def _read(url: str, *, accept: str = "application/json", attempts: int = 3) -> bytes:
    """GET with retries. Large boards (Lever, big Greenhouse tenants) intermittently
    truncate chunked responses; a retry usually returns the whole body, so prefer a
    complete read and fall back to the longest partial we saw.

    Asking for gzip is what makes that rare. This used to send `Accept-Encoding: identity`,
    which forced the biggest boards to stream 7-13 MB uncompressed and truncate every
    single time — Anthropic, OpenAI, Scale AI, Notion, Elastic and Harvey all failed on
    2026-09-09. Compressed they are 8-12x smaller, arrive whole on the first try, and take
    about half a second each. urllib does not negotiate this for us, hence `_inflate`.
    """
    best = b""
    last_exc: Exception | None = None
    for _ in range(attempts):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": UA, "Accept": accept, "Accept-Encoding": "gzip, deflate"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                encoding = resp.headers.get("Content-Encoding", "")
                try:
                    return _inflate(resp.read(), encoding)
                except IncompleteRead as exc:
                    partial = _inflate(exc.partial, encoding)
                    if len(partial) > len(best):
                        best = partial
                    last_exc = exc
        except urllib.error.HTTPError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    if best:
        return best
    raise last_exc if last_exc else RuntimeError(f"failed to read {url}")


def _repair(raw: bytes):
    """Close a truncated JSON body at its last complete array element, or None.

    Scans for the last point where a record finished directly inside an array, then
    appends a closer for every container still open there. An earlier version appended
    a fixed `}]` or `]`, which only ever closed a bare top-level array — Greenhouse and
    Ashby wrap theirs in an object (`{"jobs": [...]}`), so their partials needed `]}`
    and were discarded whole instead. Six boards a day, several MB each, thrown away.

    Byte-wise is safe: every JSON delimiter is ASCII, and UTF-8 continuation bytes are
    all >= 0x80, so a multi-byte character can never look like one.
    """
    stack: list[str] = []
    in_string = escaped = False
    cut, closers = -1, ""
    for i, byte in enumerate(raw):
        char = chr(byte)
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append(char)
        elif char in "]}":
            if not stack:
                return None  # more closers than openers; not JSON we can trust
            stack.pop()
            if stack and stack[-1] == "[":
                # A record just closed inside an array: everything up to here is whole.
                cut = i + 1
                closers = "".join("]" if o == "[" else "}" for o in reversed(stack))
    if cut < 0:
        return None  # truncated before even one record completed
    try:
        return json.loads(raw[:cut] + closers.encode())
    except json.JSONDecodeError:
        return None


def _json(url: str):
    raw = _read(url)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Salvage a truncated body rather than losing the whole fetch.
        salvaged = _repair(raw)
        if salvaged is None:
            raise
        return salvaged


_PRETTY_COMPANY = {
    "riotgames": "Riot Games",
    "epicgames": "Epic Games",
    "grafanalabs": "Grafana Labs",
    "scaleai": "Scale AI",
    "mongodb": "MongoDB",
    "gitlab": "GitLab",
    "openai": "OpenAI",
    "elevenlabs": "ElevenLabs",
    "perplexityai": "Perplexity AI",
    "runwayml": "Runway",
    "huggingface": "Hugging Face",
    "cdprojekt": "CD Projekt",
    "wealthsimple": "Wealthsimple",
    "invisibletech": "Invisible Technologies",
    "labelbox": "Labelbox",
    "testlio": "Testlio",
    "scopely": "Scopely",
    "roblox": "Roblox",
    "bungie": "Bungie",
    "remedy": "Remedy Entertainment",
    "voodoo": "Voodoo",
    "decagon": "Decagon",
    "sierra": "Sierra AI",
    "harvey": "Harvey",
    "mercor": "Mercor",
    "turing": "Turing",
}


def pretty_company(token: str) -> str:
    """ATS board tokens are slugs; they end up printed on a CV, so tidy them."""
    return _PRETTY_COMPANY.get(token.lower(), token.replace("-", " ").title())


def _as_list(value) -> list[str]:
    """Boards are inconsistent: tags arrive as a list, a dict, or a string."""
    if isinstance(value, dict):
        return [str(v) for v in value.values() if isinstance(v, (str, int))]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if isinstance(v, (str, int))]
    return [str(value)] if value else []


def _strip_html(text: str) -> str:
    # Greenhouse entity-escapes its HTML, so its tags only exist after one unescape.
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


# Epochs past this are milliseconds, not seconds: as seconds it is the year 5138, and no
# board posts a job then. Lever's createdAt is in ms, and feeding it to fromtimestamp()
# raises OSError on Windows rather than returning a silly date.
_MS_EPOCH_FLOOR = 1e11


def _iso(value) -> str:
    if not value:
        return ""
    if isinstance(value, (int, float)):
        if abs(value) >= _MS_EPOCH_FLOOR:
            value = value / 1000
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
        except (OSError, OverflowError, ValueError):
            # An unusable date must not cost us the whole board.
            return ""
    return str(value)[:10]


# --------------------------------------------------------------- remote-first


def remoteok() -> list[Posting]:
    data = _json("https://remoteok.com/api")
    out = []
    for j in data:
        if not isinstance(j, dict) or not j.get("position"):
            continue  # first element is the API legal notice
        out.append(
            Posting(
                source="remoteok",
                company=j.get("company", "").strip(),
                title=j.get("position", "").strip(),
                url=j.get("url") or j.get("apply_url", ""),
                location=j.get("location") or "Remote",
                description=_strip_html(j.get("description", ""))[:4000],
                posted_at=_iso(j.get("epoch") or j.get("date")),
                tags=[str(t) for t in (j.get("tags") or [])],
            )
        )
    return out


def remotive() -> list[Posting]:
    data = _json("https://remotive.com/api/remote-jobs?limit=200")
    return [
        Posting(
            source="remotive",
            company=j.get("company_name", "").strip(),
            title=j.get("title", "").strip(),
            url=j.get("url", ""),
            location=j.get("candidate_required_location") or "Remote",
            description=_strip_html(j.get("description", ""))[:4000],
            posted_at=_iso(j.get("publication_date")),
            tags=[j.get("category", "")] + (j.get("tags") or []),
        )
        for j in data.get("jobs", [])
    ]


def arbeitnow() -> list[Posting]:
    """EU-heavy board that exposes an explicit visa-sponsorship flag."""
    data = _json("https://www.arbeitnow.com/api/job-board-api")
    return [
        Posting(
            source="arbeitnow",
            company=j.get("company_name", "").strip(),
            title=j.get("title", "").strip(),
            url=j.get("url", ""),
            location=j.get("location", ""),
            description=_strip_html(j.get("description", ""))[:4000],
            posted_at=_iso(j.get("created_at")),
            tags=_as_list(j.get("tags")) + _as_list(j.get("job_types")),
            visa_sponsorship=bool(j.get("visa_sponsorship")),
        )
        for j in data.get("data", [])
    ]


def weworkremotely() -> list[Posting]:
    feeds = [
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
        "https://weworkremotely.com/categories/remote-design-jobs.rss",
    ]
    out = []
    for feed in feeds:
        try:
            xml = _read(feed, accept="application/rss+xml").decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        for item in re.findall(r"<item>(.*?)</item>", xml, re.S):

            def get(tag: str, _item: str = item) -> str:
                # _item is bound per iteration; a closure over `item` would make
                # every parsed field come from the last <item> in the feed.
                m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", _item, re.S)
                return m.group(1) if m else ""

            raw_title = html.unescape(get("title")).strip()
            company, _, role = raw_title.partition(":")
            out.append(
                Posting(
                    source="weworkremotely",
                    company=company.strip(),
                    title=(role or raw_title).strip(),
                    url=html.unescape(get("link")).strip(),
                    location=html.unescape(get("region")).strip() or "Remote",
                    description=_strip_html(get("description"))[:4000],
                    posted_at=_iso(get("pubDate")),
                )
            )
    return out


# ------------------------------------------------------ cross-company feeds
# Added 2026-09-17. Every ATS fetcher below only sees a board already named in
# targets.json, and VA4U's remote AI Specialist was on none of them. These two index
# remote roles across thousands of employers, one feed each, no key.

# Himalayas' search pages 20 at a time, ranked by relevance rather than date, so it is
# queried per role family and anything older than a month is dropped as it comes back.
# These are the fallback; settings.json's `search_queries` replaces them.
HIMALAYAS_QUERIES = (
    "qa engineer",
    "quality assurance",
    "test automation",
    "sdet",
    "automation engineer",
    "workflow automation",
    "ai automation",
    "ai specialist",
    "llm evaluation",
    "prompt engineer",
    "game designer",
    "game tester",
    "data analyst",
)
HIMALAYAS_PAGES = 3
HIMALAYAS_MAX_AGE_DAYS = 14


def _listed(value) -> list[str]:
    """Himalayas and Jobicy send some list fields as real lists and some as strings."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [value.strip()] if isinstance(value, str) and value.strip() else []


def himalayas(
    queries: tuple[str, ...] = HIMALAYAS_QUERIES,
    pages: int = HIMALAYAS_PAGES,
    now: datetime | None = None,
) -> list[Posting]:
    floor = (now or datetime.now(timezone.utc)).timestamp() - HIMALAYAS_MAX_AGE_DAYS * 86400
    seen: dict[str, Posting] = {}
    failures: list[Exception] = []
    for query in queries:
        for page in range(1, pages + 1):
            url = f"https://himalayas.app/jobs/api/search?q={urllib.parse.quote(query)}&page={page}"
            try:
                jobs = _json(url).get("jobs") or []
            except Exception as exc:  # noqa: BLE001 - one query must not cost the rest
                failures.append(exc)
                break
            for j in jobs:
                link = j.get("applicationLink") or j.get("guid") or ""
                try:
                    published = float(j.get("pubDate") or 0)
                except (TypeError, ValueError):
                    published = 0
                if not link or link in seen or published < floor:
                    continue
                where = _listed(j.get("locationRestrictions"))
                seen[link] = Posting(
                    source="himalayas",
                    company=(j.get("companyName") or "").strip(),
                    title=html.unescape(j.get("title") or "").strip(),
                    url=link,
                    location=_place(", ".join(where), remote=True),
                    description=_strip_html(j.get("description") or "")[:4000],
                    posted_at=_iso(published),
                    tags=_listed(j.get("categories")),
                    countries=where,
                )
            if len(jobs) < 20:
                break
    if failures and not seen:
        raise failures[-1]
    return list(seen.values())


def jobicy() -> list[Posting]:
    """The newest 100 remote roles - about a day's worth, which is what a daily scrape needs."""
    data = _json("https://jobicy.com/api/v2/remote-jobs?count=100")
    return [
        Posting(
            source="jobicy",
            company=html.unescape(j.get("companyName") or "").strip(),
            title=html.unescape(j.get("jobTitle") or "").strip(),
            url=j.get("url", ""),
            location=_place(j.get("jobGeo") or "", remote=True),
            description=_strip_html(j.get("jobDescription") or "")[:4000],
            posted_at=_iso(j.get("pubDate")),
            tags=_listed(j.get("jobIndustry")),
            # jobGeo is where they hire: "Anywhere", or a list like "Argentina, Brazil".
            countries=[c.strip() for c in (j.get("jobGeo") or "").split(",") if c.strip()],
        )
        for j in data.get("jobs", [])
    ]


# ------------------------------------------------------- company ATS boards


def greenhouse(token: str) -> list[Posting]:
    data = _json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    return [
        Posting(
            source=f"greenhouse:{token}",
            company=pretty_company(token),
            title=j.get("title", "").strip(),
            url=j.get("absolute_url", ""),
            location=(j.get("location") or {}).get("name", ""),
            description=_strip_html(j.get("content", ""))[:4000],
            posted_at=_iso(j.get("updated_at")),
        )
        for j in data.get("jobs", [])
    ]


def lever(token: str) -> list[Posting]:
    data = _json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    return [
        Posting(
            source=f"lever:{token}",
            company=pretty_company(token),
            title=j.get("text", "").strip(),
            url=j.get("hostedUrl", ""),
            location=(j.get("categories") or {}).get("location", ""),
            description=_strip_html(j.get("descriptionPlain") or j.get("description", ""))[:4000],
            posted_at=_iso(j.get("createdAt")),
            tags=[v for v in (j.get("categories") or {}).values() if isinstance(v, str)],
        )
        for j in data
    ]


def ashby(token: str) -> list[Posting]:
    # Quoted: tokens found by jobs.discover are whatever a crawled URL contained.
    data = _json(f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(token)}")
    return [
        Posting(
            source=f"ashby:{token}",
            company=pretty_company(token),
            title=j.get("title", "").strip(),
            url=j.get("jobUrl", ""),
            # Ashby carries remoteness in `isRemote`, not in `location`: VA4U's remote AI
            # Specialist came through as plain "Philippines" and scored as on-site abroad.
            location=_ashby_place(j),
            description=_strip_html(j.get("descriptionPlain", ""))[:4000],
            posted_at=_iso(j.get("publishedAt")),
            tags=[j.get("department", ""), j.get("team", "")],
        )
        for j in data.get("jobs", [])
    ]


# Workable and SmartRecruiters carry the Gulf's tech employers, which the three ATSes above
# do not - see targets.json `_gulf_2026_09_14`. Both are public JSON, no key.


def _place(*parts: str, remote: bool = False) -> str:
    place = ", ".join(p.strip() for p in parts if p and p.strip())
    return f"Remote - {place}" if remote and place else ("Remote" if remote else place)


def _ashby_place(job: dict) -> str:
    place = job.get("location", "") or ""
    remote = bool(job.get("isRemote")) or job.get("workplaceType") == "Remote"
    return place if "remote" in place.lower() else _place(place, remote=remote)


def workable(token: str) -> list[Posting]:
    """`details=true` inlines each description, so one request covers the whole board."""
    data = _json(f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true")
    return [
        Posting(
            source=f"workable:{token}",
            company=pretty_company(token),
            title=j.get("title", "").strip(),
            url=j.get("url") or j.get("shortlink", ""),
            location=_place(j.get("city", ""), j.get("country", ""), remote=j.get("telecommuting")),
            description=_strip_html(j.get("description", ""))[:4000],
            posted_at=_iso(j.get("published_on") or j.get("created_at")),
            tags=[t for t in (j.get("department"), j.get("function")) if t],
        )
        for j in data.get("jobs", [])
    ]


# The job ad sections, job-specific first: score() reads eligibility from the opening
# 1,200 characters, and a company blurb up front would push the requirements past that.
_SR_SECTIONS = ("jobDescription", "qualifications", "additionalInformation", "companyDescription")


def smartrecruiters(token: str) -> list[Posting]:
    """The listing carries no description, so each posting costs a second request. Keep
    this to small boards - a hundred-posting tenant is a hundred and one requests."""
    base = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    listing: list[dict] = []
    while True:
        page = _json(f"{base}?limit=100&offset={len(listing)}")
        got = page.get("content", [])
        listing.extend(got)
        if not got or len(listing) >= page.get("totalFound", 0):
            break
    out = []
    for j in listing:
        try:
            detail = _json(f"{base}/{j['id']}")
        except Exception:  # noqa: BLE001
            detail = {}  # one bad posting must not cost the board; it scores on its title
        sections = (detail.get("jobAd") or {}).get("sections") or {}
        texts = [_strip_html((sections.get(k) or {}).get("text", "")) for k in _SR_SECTIONS]
        loc = j.get("location") or {}
        out.append(
            Posting(
                source=f"smartrecruiters:{token}",
                company=pretty_company(token),
                title=j.get("name", "").strip(),
                url=detail.get("postingUrl")
                or f"https://jobs.smartrecruiters.com/{token}/{j['id']}",
                # fullLocation spells the country out ("Riyadh, , Saudi Arabia"); `country`
                # is an ISO code, and score() would never read "sa" as Saudi Arabia.
                location=_place(
                    *(loc.get("fullLocation") or loc.get("city", "")).split(","),
                    remote=loc.get("remote"),
                ),
                description=" ".join(t for t in texts if t)[:4000],
                posted_at=_iso(j.get("releasedDate")),
                tags=[
                    (j.get(k) or {}).get("label", "")
                    for k in ("department", "function", "typeOfEmployment")
                    if (j.get(k) or {}).get("label")
                ],
            )
        )
    return out


# ----------------------------------------------------------------- HN thread

# A "Who is hiring?" header is pipe-delimited: Company | Role | Location | Type | Salary | URL.
# The fields after the role arrive in no fixed order, so they are classified rather than
# positioned. Everything that is not an employment type, a salary or a link is location.
_HN_TYPE = re.compile(
    r"^(?:full|part)[\s-]?time\b"
    r"|^(?:contract|contractor|intern(?:ship)?|freelance|permanent|temporary|c2c|w2)\b",
    re.I,
)
_HN_SALARY = re.compile(r"[$\u20ac\u00a3\u00a5\u20b9]|\b\d{2,3}\s*k\b|\bequity\b", re.I)
_HN_URL = re.compile(r"https?://|www\.", re.I)
# Used only to reject a header whose second field is a location, not a role - see _hn_parse.
# The flag is scoped per branch on purpose: the keyword half is case-insensitive, while the
# "Chicago, IL" / "Utrecht, The Netherlands" half relies on the capitals to spot a place.
_HN_LOC_ONLY = re.compile(
    r"(?i:^(?:remote|onsite|on-site|hybrid|anywhere|worldwide|wfh)\b)"
    r"|^[A-Z][a-zA-Z .'-]+,\s*(?:[A-Z]{2}\b|[A-Z][a-z]+)"
)


def _hn_header(raw: str) -> str:
    """The first paragraph of a comment - the `Company | Role | ...` line.

    Split on the paragraph break BEFORE stripping tags. This used to take
    `text.split(".")[0]`, which cuts at the first period and so truncated every company
    whose name carries a domain: `Modash.io | Senior Product Engineer | Remote (Europe)`
    became `Modash`, the pipes went with it, and the posting ended up with its company
    name as its title.
    """
    first = re.split(r"<p>|<br\s*/?>", raw or "", maxsplit=1)[0]
    return _strip_html(first)[:300]


def _hn_parse(header: str) -> tuple[str, str, str] | None:
    """`(company, title, location)` from a header line, or None if it carries no role."""
    parts = [p.strip() for p in header.split("|")]
    if len(parts) < 2:
        return None
    company = re.sub(r"\(?\s*(?:https?://|www\.)\S*\s*\)?", "", parts[0]).strip(" -–(),")
    role = parts[1]
    # Some headers skip the role entirely (`Smarkets | Full Time | Hybrid - Onsite`).
    # A posting with no role cannot be scored, and inventing one from the location is worse.
    if not role or _HN_TYPE.match(role) or _HN_SALARY.search(role) or _HN_URL.search(role):
        return None
    if _HN_LOC_ONLY.match(role):
        return None
    where = [
        p
        for p in parts[2:]
        if p and not _HN_TYPE.match(p) and not _HN_SALARY.search(p) and not _HN_URL.search(p)
    ]
    return company[:80], role[:120], "; ".join(where)[:120]


def hackernews() -> list[Posting]:
    """Latest 'Ask HN: Who is hiring?' thread. Strong signal for AI startups."""
    # search_by_date, not search: the relevance-ranked endpoint happily returns a
    # thread from years ago, which floods the results with dead 2020 postings.
    search = _json(
        "https://hn.algolia.com/api/v1/search_by_date?query=%22Who+is+hiring%3F%22"
        "&tags=story,author_whoishiring&hitsPerPage=1"
    )
    hits = search.get("hits") or []
    if not hits:
        return []
    story_id = hits[0]["objectID"]
    thread = _json(f"https://hn.algolia.com/api/v1/items/{story_id}")

    out = []
    for child in thread.get("children") or []:
        raw = child.get("text") or ""
        text = _strip_html(raw)
        if not text or len(text) < 40:
            continue
        parsed = _hn_parse(_hn_header(raw))
        if not parsed:
            continue
        company, title, location = parsed
        out.append(
            Posting(
                source="hackernews",
                company=company,
                title=title,
                url=f"https://news.ycombinator.com/item?id={child.get('id')}",
                location=location,
                description=text[:4000],
                posted_at=_iso(child.get("created_at")),
            )
        )
    return out


# ------------------------------------------------------------------- Adzuna


def adzuna(app_id: str, app_key: str, countries: list[str], query: str) -> list[Posting]:
    out = []
    for country in countries:
        url = (
            f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
            f"?app_id={app_id}&app_key={app_key}&results_per_page=50"
            f"&what={urllib.parse.quote(query)}&content-type=application/json"
        )
        try:
            data = _json(url)
        except Exception:  # noqa: BLE001
            continue
        for j in data.get("results", []):
            out.append(
                Posting(
                    source=f"adzuna:{country}",
                    company=(j.get("company") or {}).get("display_name", "").strip(),
                    title=j.get("title", "").strip(),
                    url=j.get("redirect_url", ""),
                    location=(j.get("location") or {}).get("display_name", ""),
                    description=_strip_html(j.get("description", ""))[:4000],
                    posted_at=_iso(j.get("created")),
                )
            )
    return out


import urllib.parse  # noqa: E402  (used by adzuna above)
