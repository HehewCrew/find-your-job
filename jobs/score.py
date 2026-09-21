"""Filter and rank postings against the searcher's settings (jobs/settings.json).

The order of what matters, and what this file does with each:

  1. Eligibility - a HARD filter. A posting locked to a country you cannot work in, a
     region you are not in, or a timezone you cannot hold is dropped or heavily penalised.
     "Remote (US)" almost always means *authorized to work in the US*; for anyone who is
     not, that is a wall, not a timezone inconvenience.
  2. Location, in tiers - fully remote anywhere, then remote with hours to keep, then
     plain remote, then on-site where you live (unless you are leaving), then on-site in a
     place you named as a relocation target, then on-site anywhere else.
  3. The role - which of your role families the TITLE names, at what seniority, with the
     points you gave that family.
  4. Interests - themes you want more of (AI, gaming, climate…), from title and text.

Location tiers are spaced wider than any single bonus, so location decides the order and
everything else only sorts within a tier. A role family's points are the exception you
opt into: give one more than a tier gap and it can lift a posting across a tier.

Design note: role classification reads the TITLE, never the full description.
Matching role keywords against a 4,000-character description made almost every
posting look like a data or gaming job, because descriptions mention "data",
"game" and "AI" in passing. The description only contributes weak bonus points
and can never decide what a job *is*.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from . import settings as st
from .sources import Posting

# Defaults for callers that do not pass settings. The active settings file overrides both
# (scoring.min_score / scoring.max_per_company).
DEFAULT_MIN_SCORE = 45
MAX_PER_COMPANY = 3

# Location tiers. The gaps between adjacent tiers must stay wider than any interest bonus,
# or a themed role starts outranking a better-located one - the opposite of the ordering.
TIER_REMOTE_ANYWHERE = 75
TIER_REMOTE_OVERLAP = 55
TIER_REMOTE = 50
TIER_LOCAL = 45
TIER_RELOCATION = 32
TIER_ELSEWHERE = 4
RELOCATION_REMOTE_BONUS = 12
PREFERRED_REGION_BONUS = 8
LOCK_PENALTY = 30
LOCK_PENALTY_SPONSORED = 10
VISA_BONUS = 25
OPEN_SIGNAL_BONUS = 8
ABOVE_LEVEL_PENALTY = 40
SENIOR_LEANING_PENALTY = 15
INTERN_BONUS = 5

# Every phrase here must state a HIRING fact. Bare "worldwide" and bare "anywhere" used to
# be in this tuple and were matched against the whole description, so GIANTS Software's
# "a long-standing simulation title celebrated worldwide" put a Brno full-time office role
# in the top location tier at 145 - above a genuinely remote one. It was not one posting:
# 27 in a single scrape reached remote-anywhere on nothing but marketing copy, among them
# Proton (Skopje; Vilnius; Barcelona), Voodoo QA (Lisboa) and Moonactive (London). Location
# decides the ranking order, so a false tier here outranks every other signal combined.
REMOTE_ANY = (
    "remote anywhere",
    "anywhere in the world",
    "work from anywhere",
    "remote worldwide",
    "worldwide remote",
    "hiring worldwide",
    "global remote",
    "remote - global",
    "remote (global)",
    "fully remote",
)

# The same words ARE a hiring fact when they are the whole location field - "Worldwide",
# "Anywhere", "Global" - which is how We Work Remotely and Testlio express it. Anchored to
# the field so a description can never trigger it.
LOCATION_ANYWHERE = re.compile(
    r"^\s*(?:remote\s*[-–:(]?\s*)?(?:world\s?wide|anywhere|global|any location|any country)"
    r"\s*\)?\s*$",
    re.I,
)
# "home based" earns its place: Canonical writes every one of its ~300 remote openings
# as "Home based - EMEA", and that phrasing is the whole reason the board is in
# targets.json. It reached the sheet only because a bare "worldwide" in the
# description used to rescue it; once that was removed it fell to "on-site abroad".
REMOTE = (
    "remote",
    "distributed team",
    "telecommute",
    "home office",
    "home based",
    "home-based",
)

# Merely a working-hours expectation: ranks below fully-async, but is not a blocker.
TIMEZONE_REQS = (
    "overlap",
    "core hours",
    "cet",
    "cest",
    "gmt",
    "est ",
    "pst ",
    "eastern time",
    "pacific time",
    "central european",
)

# "UTC-5", "GMT+8", "UTC +05:30". A stated offset is a lock when every one the posting
# names is further from yours than location.max_timezone_gap_hours.
_UTC_OFFSET = re.compile(r"\b(?:utc|gmt)\s*([+-−])\s*(\d{1,2})(?::?(\d{2}))?", re.I)

# Postings that name how many years they want. Read in the description only; a count
# well above yours (years_experience + 2) costs points but never rejects.
_YEARS = re.compile(r"\b(\d{1,2})\s*\+?\s*(?:years|yrs)\b", re.I)

# US-based roles, remote or on-site, are rejected outright unless you can work in the US.
# A penalty was not enough - the first 148-lead sheet after Himalayas and discovery had 33
# US-only remote roles, many above 100, plus on-site San Francisco, New York and Atlanta
# roles scattered through the rest.
US_NAMES = {"us", "u.s.", "usa", "u.s.a.", "united states", "united states of america"}
# State codes that are not also ISO country codes, so "Weirton, WV" alone is the US.
_STATE_CODES = "AK|CT|DC|FL|HI|IA|KS|MI|NH|NJ|NM|NV|NY|ND|OH|OK|OR|RI|TX|UT|VT|WA|WV|WI|WY"
# The rest collide with a country: TN is Tunisia, IN India, CA Canada, DE Germany, MA
# Morocco. "Pune, IN" matched Indiana on the first try. These count only next to a known
# US city or in a location that names the US outright.
_AMBIGUOUS_CODES = "AL|AR|AZ|CA|CO|DE|GA|ID|IL|IN|KY|LA|MA|MD|ME|MN|MO|MS|MT|NC|NE|PA|SC|SD|TN|VA"
# No "Georgia" (a country) and no bare "Washington" (usually the state, but "Washington,
# DC" is caught by its code anyway). Cities are the ones boards write without a state.
_STATE_NAMES = (
    "Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Hawaii"
    "|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts"
    "|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire"
    "|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon"
    "|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont"
    "|Virginia|West Virginia|Wisconsin|Wyoming"
)
_US_CITIES = (
    "San Francisco|SF Bay Area|Bay Area|New York City|NYC|Brooklyn|Los Angeles|Seattle|Boston"
    "|Chicago|Austin|Atlanta|Denver|Boulder|Dallas|Houston|Miami|San Diego|San Jose|Palo Alto"
    "|Mountain View|Menlo Park|Oakland|Sunnyvale|Santa Clara|Redwood City|San Carlos"
    "|Scotts Valley|Torrance|Irvine|Santa Monica|Philadelphia|Pittsburgh|Salt Lake City"
    "|Phoenix|Minneapolis|Detroit|Nashville|Raleigh|Durham|Portland, Oregon|Washington DC"
)
# One US place, with the city in front of a state consumed along with it ("Weirton, WV",
# "Provo UT").
_US_COUNTRY = r"\b(?:United States(?: of America)?|USA|U\.S\.A?\.?|US)\b"
US_PLACE = re.compile(
    rf"(?:[A-Z][\w.'-]*(?: [A-Z][\w.'-]*)*,?\s+)?\b(?:{_STATE_CODES})\b"
    rf"|{_US_COUNTRY}"
    rf"|\b(?:{_US_CITIES})(?:,?\s*(?:{_STATE_CODES}|{_AMBIGUOUS_CODES}))?\b"
    rf"|\b(?:{_STATE_NAMES})\b"
)
_AMBIGUOUS_PLACE = re.compile(
    rf"(?:[A-Z][\w.'-]*(?: [A-Z][\w.'-]*)*,?\s+)?\b(?:{_AMBIGUOUS_CODES})\b"
)
# Words a location field adds around a place without naming another one.
_LOCATION_FILLER = re.compile(
    r"\b(?:remote|hybrid|on-?site|in-?office|office|only|based|anywhere in|city|metro"
    r"|area|hq|headquarters|full[- ]?time|us-based)\b|[^\w]+",
    re.I,
)


def us_located(location: str) -> bool:
    """True when every place the location field names is in the US.

    Split on `;`, `|`, `/`, `•` and " or ", so "Austin, TX; London" and "US or Europe" are
    kept. Within a part, US places and filler are removed; anything left over is somewhere
    else. A comma list like "Australia, Canada, United States" therefore survives.
    """
    found = False
    names_us = bool(re.search(_US_COUNTRY, location or ""))
    for part in re.split(r"[;|/•]| or ", location or ""):
        rest, n = US_PLACE.subn(" ", part)
        if names_us:
            rest, extra = _AMBIGUOUS_PLACE.subn(" ", rest)
            n += extra
        leftover = _LOCATION_FILLER.sub("", rest)
        if n:
            if leftover:
                return False
            found = True
        elif leftover:
            return False
    return found


# Read against the original-case text: lowercased, "US only" is also "contact us only".
_US_CASED = r"(?:US|U\.S\.|USA|U\.S\.A\.|United States|UNITED STATES)"
US_ONLY_TEXT = re.compile(
    rf"\b{_US_CASED}[\s-]*(?:[Oo]nly|ONLY)\b"
    rf"|\b(?i:must (?:be|reside|live) (?:based )?in the) {_US_CASED}\b"
)

# Everything before the first comma, bracket, pipe or spaced dash. That prefix is what
# names the job; the rest is the team, product or region it belongs to.
_TITLE_TAIL = re.compile(r"[,(\[|]|\s[-–—]\s")


@dataclass
class Scored:
    posting: Posting
    score: int
    variant: str
    reasons: list[str]
    rejected: str = ""
    # Filled in by the caller from jobs.tailor - the matched/gap keyword analysis for
    # this posting. Kept as a plain dict so scoring stays independent of tailoring;
    # `models` -> `score` -> `tailor` is the direction, and it must not reverse.
    tailor: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.rejected


def _cfg(settings: st.Settings | None) -> st.Settings:
    return settings if settings is not None else st.current()


def _has(text: str, terms) -> bool:
    return any(t in text for t in terms)


def _head(title: str) -> str:
    return _TITLE_TAIL.split(title, 1)[0].strip()


def excluded_by(title: str, settings: st.Settings | None = None) -> re.Match | None:
    """The relevance gate, in one place: an excluded title word anywhere, or an excluded
    department heading the title. "Senior Analytics Engineer, Finance" is an analytics job
    in the finance org, so departments only count before the first comma or dash.

    Returns the offending match so the caller can ask whether a matched role allows it."""
    s = _cfg(settings)
    return (s.exclude_titles and s.exclude_titles.search(title)) or (
        s.exclude_departments and s.exclude_departments.search(_head(title))
    )


def _families(title: str, settings: st.Settings | None = None) -> set[str]:
    s = _cfg(settings)
    raw = {r.name for r in s.roles if r.titles.search(title)}
    # `not_titles` vetoes a family, unless the title also names one of `not_titles_unless`:
    # "Software Engineer, AI Evals" builds an evals product, but "Software Engineer in
    # Test, AI Evals" is QA work and keeps its AI credit.
    return {
        r.name
        for r in s.roles
        if r.name in raw
        and not (r.not_titles and r.not_titles.search(title) and not raw & set(r.not_titles_unless))
    }


def _interests(title: str, desc: str, company: str, s: st.Settings) -> dict[str, str]:
    """{interest name: "title" | "text"} for every interest this posting shows."""
    found: dict[str, str] = {}
    for i in s.interests:
        if i.terms.search(title):
            found[i.name] = "title"
        elif i.terms.search(desc[:1500]) or (i.companies and i.companies.search(company or "")):
            found[i.name] = "text"
    return found


def is_interest(name: str, title: str, desc: str, company: str = "", settings=None) -> bool:
    s = _cfg(settings)
    return name in _interests(title.lower(), desc.lower(), company, s)


def pick_variant(
    families: set[str],
    title: str,
    desc: str,
    company: str = "",
    settings: st.Settings | None = None,
) -> str:
    """The CV variant for a posting: the first matching role in settings order, then the
    first of that role's cv_rules whose conditions all hold."""
    s = _cfg(settings)
    found = _interests(title, desc, company, s)
    text = f"{title} {company} {desc[:1200]}"
    for role in s.roles:
        if role.name not in families:
            continue
        for rule in role.cv_rules:
            if rule.title and not rule.title.search(title):
                continue
            if rule.text and not rule.text.search(text):
                continue
            if rule.interest and rule.interest not in found:
                continue
            return rule.cv
        return role.cv
    return s.default_cv


def _us_only(posting: Posting) -> bool:
    countries = [c.strip().lower() for c in posting.countries]
    if countries and all(c in US_NAMES for c in countries):
        return True
    text = f"{posting.title} {(posting.description or '')[:1200]}"
    return us_located(posting.location) or bool(US_ONLY_TEXT.search(text))


def _timezone_lock(text: str, s: st.Settings) -> str:
    gaps = []
    for sign, hours, minutes in _UTC_OFFSET.findall(text):
        offset = int(hours) + int(minutes or 0) / 60
        offset = -offset if sign in "-−" else offset
        gaps.append((abs(offset - s.utc_offset), f"utc{sign}{hours}"))
    if gaps and min(g for g, _ in gaps) > s.max_timezone_gap:
        return min(gaps)[1]
    return ""


def _level_rank(level: str) -> int:
    return st.LEVELS.index(level)


def score(posting: Posting, settings: st.Settings | None = None) -> Scored:
    s = _cfg(settings)
    title = posting.title.lower()
    desc = (posting.description or "").lower()
    loc = (posting.location or "").lower()
    text = posting.haystack
    reasons: list[str] = []
    pts = 0

    if not posting.company or not posting.title:
        return Scored(posting, 0, "", [], rejected="incomplete posting")

    # --- relevance gate: must be one of your role families -------------------
    families = _families(title, s)
    roles = [r for r in s.roles if r.name in families]
    excluded = excluded_by(title, s)
    if excluded and not any(
        r.allow_excluded and r.allow_excluded.fullmatch(excluded.group(0).strip()) for r in roles
    ):
        return Scored(posting, 0, "", [], rejected="different profession / unfamiliar stack")
    if not families:
        return Scored(posting, 0, "", [], rejected="not a target role family")

    # --- seniority, judged per role family, never globally -------------------
    # Five years of paid QA makes "Senior QA Engineer" credible; a portfolio of hobby games
    # does not make "Senior Game Designer" credible. Each family carries its own ceiling.
    level = st.level_of(title)
    found = _interests(title, desc, posting.company, s)
    if level == "intern":
        allowed = [
            r for r in roles if r.internships is True or (r.internships and r.internships in found)
        ]
        if not allowed:
            return Scored(posting, 0, "", [], rejected="internship, not wanted for this role")
        tag = next((r.internships for r in allowed if isinstance(r.internships, str)), "")
        pts += INTERN_BONUS
        reasons.append(f"intern-ok({tag})" if tag else "intern-ok")
    elif level:
        over = [r for r in roles if _level_rank(level) > _level_rank(r.max_level)]
        if over and len(over) == len(roles):
            hard = next((r for r in over if r.above_level == "reject"), None)
            if hard:
                label = hard.name.replace("_", " ")
                band = st.LEVEL_WORDS.get(hard.max_level, hard.max_level)
                return Scored(posting, 0, "", [], rejected=f"{label} above {band} level")
            pts -= ABOVE_LEVEL_PENALTY
            reasons.append("above-level")

    remote_any = _has(text, REMOTE_ANY) or bool(LOCATION_ANYWHERE.match(loc))
    remote = remote_any or _has(text, REMOTE) or "remote" in loc
    # "Austin, TX" never names its country, so a US home reuses the state/city detection.
    at_home = bool(s.home_places and s.home_places.search(loc)) or (
        s.home_country == "united states" and us_located(posting.location)
    )

    # --- HARD FILTERS -----------------------------------------------------------
    if at_home and not remote and s.reject_onsite_at_home:
        return Scored(posting, 0, "", [], rejected=f"on-site in {s.home_country.title()}")
    if (
        not s.can_work_in("united states")
        and not s.would_go_to("united states")
        and _us_only(posting)
    ):
        return Scored(posting, 0, "", [], rejected="US only")

    # --- eligibility locks ----------------------------------------------------
    # Eligibility is read near the top of the posting, where it is actually stated.
    # Scanning the whole description punished good roles for one stray "hybrid".
    lock_zone = f"{title} {loc} {desc[:1200]}"
    names_yours = bool(s.authorized_mention and s.authorized_mention.search(lock_zone))
    locks = [
        k
        for k in s.auth_locks
        if k in lock_zone and not (k in st.GENERIC_LOCK_PHRASES and names_yours)
    ]
    if not at_home:
        locks += [k for k in s.onsite_locks if k in lock_zone]

    # A region lock outranks any "fully remote" wording: one employer's nine "Freelance |
    # 8-20 hrs/week | Remote (EU/UK)" posts took the top of a review sheet because
    # "fully remote" set remote_any, which then suppressed the eligibility penalty below.
    region_lock = s.region_lock.search(lock_zone) if s.region_lock else None
    if region_lock:
        remote_any = False
        locks.append(region_lock.group(0).strip())
    tz_lock = _timezone_lock(lock_zone, s)
    if tz_lock:
        remote_any = False
        locks.append(tz_lock)
    # A feed's own hiring-country list is the plainest lock there is. Himalayas' first
    # trial put 154 leads on the sheet, every one restricted to a country, led by
    # "Remote - United States" at 162 - none of the phrases above said so in prose.
    if posting.countries and not any(
        st.canonical_country(c) in s.open_entries for c in posting.countries
    ):
        # A lock is a drop, except in a country you said you would go to: those stay on
        # the sheet with the penalty, since the move is the point.
        if not any(s.would_go_to(c) for c in posting.countries):
            where = ", ".join(posting.countries[:3])
            return Scored(posting, 0, "", [], rejected=f"hires only in {where}")
        remote_any = False
        locks.append(f"hires only in {', '.join(posting.countries[:3])}")
    relocation_target = bool(s.relocation) and bool(
        s.relocation.search(f"{loc} {title}") or s.relocation.search(desc[:1200])
    )

    # --- location, in tiers ---------------------------------------------------
    if remote_any:
        pts += TIER_REMOTE_ANYWHERE
        reasons.append("remote-anywhere")
    elif remote and _has(lock_zone, TIMEZONE_REQS):
        # Remote, just expected to keep someone else's hours.
        pts += TIER_REMOTE_OVERLAP
        reasons.append("remote+overlap")
    elif remote:
        pts += TIER_REMOTE
        reasons.append("remote")
    elif at_home:
        pts += TIER_LOCAL
        reasons.append("local")
    elif relocation_target:
        # On-site, but in one of the places you named. Weighted so a plain role here
        # clears DEFAULT_MIN_SCORE on its own - the whole tier would be filtered out
        # otherwise, which is what happened when this was worth less.
        pts += TIER_RELOCATION
        reasons.append("relocation-target-based")
    else:
        # Nowhere you asked for: kept, ranked last, and below the default threshold
        # unless something else recommends it.
        pts += TIER_ELSEWHERE
        reasons.append("on-site abroad")

    if relocation_target and "relocation-target-based" not in reasons:
        # A remote role that is also in a named target: the easiest overlap there is.
        pts += RELOCATION_REMOTE_BONUS
        reasons.append("relocation-target")
    elif not relocation_target and s.preferred_regions and s.preferred_regions.search(text):
        pts += PREFERRED_REGION_BONUS
        reasons.append("preferred-region")

    # An eligibility lock is a blocker, not a preference - unless they sponsor.
    sponsors = posting.visa_sponsorship and s.needs_visa_sponsorship
    if locks and not remote_any:
        pts -= LOCK_PENALTY if not sponsors else LOCK_PENALTY_SPONSORED
        reasons.append(f"eligibility({locks[0]})")
    if sponsors:
        pts += VISA_BONUS
        reasons.append("visa-sponsorship")
    if _has(text, s.open_signals):
        pts += OPEN_SIGNAL_BONUS

    # --- role families ----------------------------------------------------------
    covered = {r.interest for r in roles if r.interest}
    for r in roles:
        if r.points < 0 and r.waive_if_interest and r.waive_if_interest in found:
            reasons.append(f"{r.name}(exempt: {r.waive_if_interest})")
            continue
        if r.points:
            pts += r.points
            reasons.append(r.reason)
        if r.junior_bonus and level in ("intern", "junior"):
            pts += r.junior_bonus
            reasons.append(f"entry-level {r.name.replace('_', ' ')}")

    # --- interests ----------------------------------------------------------------
    # Skipped for a theme a matched role already pays for - an "AI QA" role family should
    # not collect the AI bonus a second time for the word that made it one.
    for i in s.interests:
        where = found.get(i.name)
        if not where or i.name in covered:
            continue
        pts += i.title_points if where == "title" else i.text_points
        reasons.append(f"{i.name}-title" if where == "title" else f"{i.name}-mentioned")

    for b in s.description_bonus:
        if b.terms.search(desc):
            pts += b.points

    # --- years asked for, in the description --------------------------------------
    # A "10 years" buried in a description is often boilerplate about the company, so it
    # costs less than a title would.
    if s.years_experience:
        asked = [int(n) for n in _YEARS.findall(desc[:2000])]
        if any(s.years_experience + 2 <= n <= 30 for n in asked):
            pts -= SENIOR_LEANING_PENALTY
            reasons.append("senior-leaning")

    return Scored(posting, pts, pick_variant(families, title, desc, posting.company, s), reasons)


def dedupe_key(company: str, title: str) -> str:
    """Collapse the same role reposted per-location or per-board.

    Both the in-run deduper and the already-tracked check must use THIS function -
    using two different normalisations silently re-adds jobs on every daily run.
    """
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", (title or "").lower())
    t = re.split(r" [-–|,] ", t)[0]
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", s)  # noqa: E731
    return f"{norm((company or '').lower())}::{norm(t)}"


def _dedupe_key(p: Posting) -> str:
    return dedupe_key(p.company, p.title)


def rank(
    postings: list[Posting],
    *,
    min_score: int | None = None,
    per_company: int | None = None,
    exclude: set[str] | None = None,
    settings: st.Settings | None = None,
) -> list[Scored]:
    """Score, dedupe and cap. `exclude` holds dedupe keys already in jobtrack.

    Excluding them HERE, before the per-company cap, is the whole point of the
    parameter - the caller filtering afterwards is what the cap is supposed to
    survive. The cap is a daily quota, and it must be spent on leads you can still
    act on: on 2026-08-21 three already-applied Testlio roles filled Testlio's three
    slots and hid every fresh one behind them.

    It seeds the same `seen` set the in-run deduper uses, so both sides go through
    `_dedupe_key` and cannot drift apart.
    """
    s = _cfg(settings)
    min_score = s.min_score if min_score is None else min_score
    per_company = s.max_per_company if per_company is None else per_company
    seen: set[str] = set(exclude or ())
    out: list[Scored] = []
    for p in postings:
        key = _dedupe_key(p)
        if key in seen:
            continue
        seen.add(key)
        scored = score(p, s)
        if scored.ok and scored.score >= min_score:
            out.append(scored)
    out.sort(key=lambda x: x.score, reverse=True)

    # Keep each employer's best few. A single company posting one role per domain can
    # otherwise fill the sheet and push better-matched leads below the fold; the review
    # sheet is a scarce resource, and the 6th listing from one employer never earns a slot.
    if per_company > 0:
        kept: list[Scored] = []
        per: Counter[str] = Counter()
        for x in out:
            name = (x.posting.company or "").strip().lower()
            if name:
                if per[name] >= per_company:
                    continue
                per[name] += 1
            kept.append(x)
        out = kept
    return out
