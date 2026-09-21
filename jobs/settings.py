"""Who is searching, and for what: the settings every scoring decision reads.

    jobs/settings.json           yours - gitignored, copy it from the example
    jobs/settings.example.json   tracked template; used as-is when yours is missing
    $JOBHUNT_SETTINGS            point at any other file (the tests do)

`score.py` used to hard-code one person's search: a home country to leave, the countries
they could work in, their relocation targets, the role families and seniority bands they
could win. None of that is code. It lives here as data, so the same scorer ranks leads for
a QA engineer leaving Tunisia, a data analyst staying in Ohio, or anyone else.

Phrases, not regexes. Every list of words in the file is a list of *phrases*, matched
case-insensitively on word boundaries:

    "qa"              matches "QA Engineer", never "Qatar"
    "game design*"    `*` is "any letters": "Game Designer", "Game Designers"
    "re:\\bsdet\\b"   the escape hatch - a raw regular expression

A trailing `*` matters more than it looks: without it "game design" never matches
"Game Designer", because the "e" after "design" defeats the closing word boundary.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS_PATH = HERE / "settings.json"
EXAMPLE_PATH = HERE / "settings.example.json"
ENV_VAR = "JOBHUNT_SETTINGS"

# Seniority bands, lowest first. A role's `max_level` is one of these.
LEVELS = ("intern", "junior", "mid", "senior", "lead", "principal")
# How a band reads in a sentence: "game design above entry level".
LEVEL_WORDS = {"intern": "internship", "junior": "entry"}

# Title words that place a posting in a band. Checked lowest band first, so "Senior Game
# Design Intern" is an internship - the entry-level word is the one that describes the job.
# A title with none of these has no stated level and passes any `max_level`.
_LEVEL_MARKERS: tuple[tuple[str, re.Pattern], ...] = (
    ("intern", re.compile(r"\bintern(ship)?s?\b|\bworking student\b|\bwerkstudent\b", re.I)),
    (
        "junior",
        re.compile(
            r"\b(junior|jr\.?|entry[- ]level|entry|graduate|grad|trainee|apprentice|associate)\b",
            re.I,
        ),
    ),
    ("senior", re.compile(r"\b(senior|sr\.?|expert|ii|iii)\b", re.I)),
    ("lead", re.compile(r"\b(lead|manager)\b", re.I)),
    (
        "principal",
        re.compile(r"\b(principal|staff|head|director|chief|vp)\b", re.I),
    ),
)

# Countries named several ways in job feeds. Keys and values are lowercase; every lookup
# goes through canonical_country() so "USA", "U.S." and "United States" compare equal.
COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "united states": ("us", "u.s.", "usa", "u.s.a.", "united states of america", "america"),
    "united kingdom": ("uk", "u.k.", "great britain", "britain", "england", "scotland", "wales"),
    "netherlands": ("the netherlands", "holland", "nederland"),
    "germany": ("deutschland",),
    "spain": ("españa", "espana"),
    "united arab emirates": ("uae", "emirates"),
    "saudi arabia": ("ksa", "kingdom of saudi arabia"),
    "vietnam": ("viet nam",),
    "laos": ("lao pdr",),
    "south korea": ("korea", "republic of korea"),
    "czechia": ("czech republic",),
    "timor-leste": ("east timor",),
    "turkey": ("türkiye", "turkiye"),
}
_ALIAS_TO_COUNTRY = {a: c for c, aliases in COUNTRY_ALIASES.items() for a in aliases}

# Shorthands for `authorized_countries`: an EU citizen can write "EU" instead of 27 names.
EU_COUNTRIES = (
    "austria", "belgium", "bulgaria", "croatia", "cyprus", "czechia", "denmark", "estonia",
    "finland", "france", "germany", "greece", "hungary", "ireland", "italy", "latvia",
    "lithuania", "luxembourg", "malta", "netherlands", "poland", "portugal", "romania",
    "slovakia", "slovenia", "spain", "sweden",
)  # fmt: skip
COUNTRY_GROUPS = {
    "eu": EU_COUNTRIES,
    "european union": EU_COUNTRIES,
    "eea": (*EU_COUNTRIES, "iceland", "liechtenstein", "norway"),
}

# Hiring-list entries that mean "no restriction".
OPEN_WORDS = ("worldwide", "world wide", "anywhere", "anywhere in the world", "global")

# Phrases that lock a posting to one country's workforce. A lock on a country you are
# authorized in is no lock at all, so these are dropped per user - an American's "US only"
# is the best news in the posting, not a blocker.
COUNTRY_LOCK_PHRASES: dict[str, tuple[str, ...]] = {
    "united states": (
        "us only",
        "u.s. only",
        "usa only",
        "united states only",
        "us-based",
        "must be based in the us",
        "authorized to work in the united states",
        "us citizen",
    ),
    "canada": ("canada only",),
    "united kingdom": ("uk only",),
    # Gulf localization quotas (Saudization, Emiratization) reserve roles for nationals.
    "saudi arabia": ("saudi national",),
    "united arab emirates": ("emirati national", "uae national"),
    "qatar": ("qatari national",),
    "kuwait": ("kuwaiti national",),
    "oman": ("omani national",),
    "bahrain": ("bahraini national",),
    "gcc": ("gcc national",),
}
# Locks that name no country. Read against the top of the posting only - see score().
GENERIC_LOCK_PHRASES = (
    "must be authorized to work",
    "work authorization in",
    "must reside in",
    "must be located in",
    "security clearance",
)
# These restrict *where you sit*, which is only a blocker when it is not where you live.
ONSITE_LOCK_PHRASES = ("onsite only", "on-site only", "hybrid")

# Prose region locks. Each is paired with the regions it lets in: "EU/UK only" locks out
# anyone who is in neither. Deliberately absent: EMEA, Africa and "worldwide" as bare
# words - Canonical's "Home based - EMEA" must not read as a lock for someone in EMEA.
_REGION_LOCK_FRAGMENTS: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"eu", "uk"}), r"\b(eu\s*/\s*uk|uk\s*/\s*eu|eu\s+or\s+uk|uk\s+or\s+eu)\b"),
    *(
        (
            frozenset({r}),
            rf"\b(?:based|located|reside|residing|living|work)\w*\s+(?:in|within)\s+the\s+{r}\b"
            rf"|\bremote\s*[-(]\s*{r}\b|\b{r}\s+only\b",
        )
        for r in ("eu", "eea", "uk")
    ),
    (
        frozenset({"apac"}),
        r"\bremote\s*[-(]\s*apac\b|\bapac\s+only\b|\bapac\s+region\b"
        # "Home Based - APAC" is how Canonical writes an APAC-only remote role. Anchored to
        # the region prefix so an incidental "we have teams in APAC" does not lock a posting.
        r"|\b(?:home[\s-]?based|remote|based)\s*[-–:]?\s*apac\b",
    ),
    (frozenset({"americas"}), r"\bremote\s*[-(]\s*americas\b|\bamericas\s+only\b"),
    (frozenset({"latam"}), r"\bremote\s*[-(]\s*latam\b|\blatam\s+only\b"),
    (frozenset({"north america", "americas"}), r"\bnorth america\s+only\b"),
    (frozenset({"south america", "americas", "latam"}), r"\bsouth america\s+only\b"),
    (
        frozenset({"north america", "south america", "americas"}),
        r"\bnorth\s*(?:&|and)\s*south america\b",
    ),
    (frozenset({"emea"}), r"\bemea\s+only\b"),
)

# The LLM providers jobs.llm knows how to call, and the env var each reads its key from.
LLM_PROVIDERS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    # Any server speaking OpenAI's /chat/completions: OpenRouter, Groq, Mistral, a local
    # Ollama or LM Studio. Needs `base_url`.
    "openai-compatible": "LLM_API_KEY",
}


class SettingsError(ValueError):
    """A settings file that cannot be scored against. The message names the key."""


def canonical_country(name: str) -> str:
    low = re.sub(r"\s+", " ", (name or "").strip().lower())
    return _ALIAS_TO_COUNTRY.get(low, low)


def phrase(p: str) -> str:
    """One settings phrase as a regex fragment. See the module docstring for the syntax."""
    p = p.strip()
    if chr(8) in p:
        # JSON reads a backslash-b as a backspace character, which silently turns a word
        # boundary into a character no title contains - and disables the whole pattern.
        raise SettingsError(
            f"{p!r} contains a backspace: a word boundary is written with two backslashes in JSON"
        )
    if p.startswith("re:"):
        return f"(?:{p[3:]})"
    parts = [re.escape(chunk) for chunk in p.split("*")]
    body = r"\w*".join(parts)
    tail = "" if p.endswith("*") else r"\b"
    return rf"\b{body}{tail}"


def phrases(items, *, key: str) -> re.Pattern | None:
    """Compile a phrase list into one case-insensitive pattern; None when it is empty."""
    if items in (None, ""):
        return None
    if isinstance(items, str) or not all(isinstance(i, str) and i.strip() for i in items):
        raise SettingsError(f"{key}: expected a list of non-empty phrases, got {items!r}")
    if not items:
        return None
    try:
        return re.compile("|".join(phrase(i) for i in items), re.I)
    except re.error as exc:
        raise SettingsError(f"{key}: {exc}") from None


def level_of(title: str) -> str | None:
    """The seniority band a title states, or None when it states none."""
    for name, pat in _LEVEL_MARKERS:
        if pat.search(title):
            return name
    return None


@dataclass
class CvRule:
    """Send a role to a different CV variant when the posting looks a certain way."""

    cv: str
    title: re.Pattern | None = None
    text: re.Pattern | None = None
    interest: str = ""


@dataclass
class Role:
    """One role family you would apply to."""

    name: str
    titles: re.Pattern
    cv: str
    points: int = 0
    reason: str = ""
    not_titles: re.Pattern | None = None
    not_titles_unless: list[str] = field(default_factory=list)
    cv_rules: list[CvRule] = field(default_factory=list)
    max_level: str = "principal"
    above_level: str = "penalise"
    internships: bool | str = False
    junior_bonus: int = 0
    allow_excluded: re.Pattern | None = None
    interest: str = ""
    waive_if_interest: str = ""


@dataclass
class Interest:
    """A theme worth extra points wherever it shows up: AI, gaming, climate, fintech…"""

    name: str
    terms: re.Pattern
    companies: re.Pattern | None = None
    title_points: int = 0
    text_points: int = 0


@dataclass
class Bonus:
    terms: re.Pattern
    points: int


@dataclass
class Target:
    """A place you would relocate to."""

    country: str
    places: re.Pattern


@dataclass
class Settings:
    path: Path | None
    home_country: str
    home_places: re.Pattern | None
    authorized: set[str]
    regions: set[str]
    utc_offset: float
    years_experience: int
    needs_visa_sponsorship: bool
    reject_onsite_at_home: bool
    targets: list[Target]
    accept_locked: set[str]
    max_timezone_gap: float
    preferred_regions: re.Pattern | None
    roles: list[Role]
    exclude_titles: re.Pattern | None
    exclude_departments: re.Pattern | None
    interests: list[Interest]
    description_bonus: list[Bonus]
    search_queries: list[str]
    min_score: int
    max_per_company: int
    skills: dict[str, str]
    gaps: dict[str, str]
    weak_skills: set[str]
    llm: dict
    # Derived once, so score() never rebuilds a pattern per posting.
    open_entries: set[str] = field(default_factory=set)
    auth_locks: tuple[str, ...] = ()
    authorized_mention: re.Pattern | None = None
    onsite_locks: tuple[str, ...] = ()
    region_lock: re.Pattern | None = None
    relocation: re.Pattern | None = None
    open_signals: tuple[str, ...] = ()

    @property
    def is_example(self) -> bool:
        return self.path is not None and self.path.resolve() == EXAMPLE_PATH.resolve()

    @property
    def role_names(self) -> list[str]:
        return [r.name for r in self.roles]

    @property
    def default_cv(self) -> str:
        return self.roles[0].cv if self.roles else ""

    def role(self, name: str) -> Role | None:
        return next((r for r in self.roles if r.name == name), None)

    def interest(self, name: str) -> Interest | None:
        return next((i for i in self.interests if i.name == name), None)

    def can_work_in(self, country: str) -> bool:
        return canonical_country(country) in self.authorized

    def would_go_to(self, country: str) -> bool:
        c = canonical_country(country)
        return c in self.accept_locked or any(t.country == c for t in self.targets)

    def cv_names(self) -> set[str]:
        names = {r.cv for r in self.roles}
        names |= {rule.cv for r in self.roles for rule in r.cv_rules}
        return names

    def missing_variants(self, profile: dict) -> list[str]:
        """CV variants these settings can choose that profile.json does not define."""
        have = set(profile.get("variants", {}))
        return sorted(n for n in self.cv_names() if n not in have)


# -- building --------------------------------------------------------------


def _get(d: dict, key: str, typ, default, where: str):
    value = d.get(key, default)
    if typ is float and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    if value is not None and not isinstance(value, typ):
        name = typ.__name__ if isinstance(typ, type) else "/".join(t.__name__ for t in typ)
        raise SettingsError(f"{where}{key}: expected {name}, got {value!r}")
    return value


def _strings(d: dict, key: str, where: str) -> list[str]:
    items = d.get(key) or []
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        raise SettingsError(f"{where}{key}: expected a list of strings")
    return [i for i in items if i.strip()]


def _skill_map(raw, key: str) -> dict[str, str]:
    """{"Canonical name": ["phrase", ...]} -> {"Canonical name": regex}."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise SettingsError(f"tailoring.{key}: expected an object of name -> phrases")
    out: dict[str, str] = {}
    for name, terms in raw.items():
        if name.startswith("_"):
            continue
        terms = [name] if terms in (None, [], "") else terms
        pat = phrases(terms, key=f"tailoring.{key}.{name}")
        out[name] = pat.pattern
    return out


def _role(raw: dict, i: int) -> Role:
    where = f"roles[{i}]."
    if not isinstance(raw, dict):
        raise SettingsError(f"roles[{i}]: expected an object")
    name = _get(raw, "name", str, "", where)
    if not name:
        raise SettingsError(f"{where}name: every role needs a name")
    where = f"roles[{name}]."
    titles = phrases(_strings(raw, "titles", where), key=f"{where}titles")
    if titles is None:
        raise SettingsError(f"{where}titles: list the job titles this role goes by")
    cv = _get(raw, "cv", str, "", where)
    if not cv:
        raise SettingsError(f"{where}cv: name the profile.json variant this role uses")
    max_level = _get(raw, "max_level", str, "principal", where)
    if max_level not in LEVELS:
        raise SettingsError(f"{where}max_level: one of {', '.join(LEVELS)}, got {max_level!r}")
    above = _get(raw, "above_level", str, "penalise", where)
    if above not in ("penalise", "reject"):
        raise SettingsError(f"{where}above_level: 'penalise' or 'reject', got {above!r}")
    internships = raw.get("internships", False)
    if not isinstance(internships, (bool, str)):
        raise SettingsError(f"{where}internships: true, false, or the name of an interest")
    rules = []
    for j, r in enumerate(raw.get("cv_rules") or []):
        w = f"{where}cv_rules[{j}]."
        if not isinstance(r, dict) or not r.get("cv"):
            raise SettingsError(f"{w}cv: each rule names the variant it switches to")
        rules.append(
            CvRule(
                cv=r["cv"],
                title=phrases(_strings(r, "title", w), key=f"{w}title"),
                text=phrases(_strings(r, "text", w), key=f"{w}text"),
                interest=_get(r, "interest", str, "", w),
            )
        )
        if not (rules[-1].title or rules[-1].text or rules[-1].interest):
            raise SettingsError(f"{w}: a rule needs a title, text or interest condition")
    return Role(
        name=name,
        titles=titles,
        cv=cv,
        points=_get(raw, "points", int, 0, where),
        reason=_get(raw, "reason", str, "", where) or f"{name}-role",
        not_titles=phrases(_strings(raw, "not_titles", where), key=f"{where}not_titles"),
        not_titles_unless=_strings(raw, "not_titles_unless", where),
        cv_rules=rules,
        max_level=max_level,
        above_level=above,
        internships=internships,
        junior_bonus=_get(raw, "junior_bonus", int, 0, where),
        allow_excluded=phrases(_strings(raw, "allow_excluded", where), key=f"{where}allow"),
        interest=_get(raw, "interest", str, "", where),
        waive_if_interest=_get(raw, "waive_if_interest", str, "", where),
    )


def _interest(raw: dict, i: int) -> Interest:
    where = f"interests[{i}]."
    name = _get(raw, "name", str, "", where)
    if not name:
        raise SettingsError(f"{where}name: every interest needs a name")
    terms = phrases(_strings(raw, "terms", where), key=f"interests[{name}].terms")
    if terms is None:
        raise SettingsError(f"interests[{name}].terms: list the words that signal it")
    return Interest(
        name=name,
        terms=terms,
        companies=phrases(_strings(raw, "companies", where), key=f"interests[{name}].companies"),
        title_points=_get(raw, "title_points", int, 0, where),
        text_points=_get(raw, "text_points", int, 0, where),
    )


def _target(raw, i: int) -> Target:
    where = f"location.relocation_targets[{i}]."
    if isinstance(raw, str):
        raw = {"country": raw}
    country = canonical_country(_get(raw, "country", str, "", where))
    if not country:
        raise SettingsError(f"{where}country: required")
    places = _strings(raw, "places", where)
    if not places:
        # No cities named: the whole country counts, under every name it goes by.
        places = [country, *COUNTRY_ALIASES.get(country, ())]
    return Target(country=country, places=phrases(places, key=f"{where}places"))


def from_dict(data: dict, path: Path | None = None) -> Settings:
    if not isinstance(data, dict):
        raise SettingsError("the settings file must be a JSON object")
    you = data.get("you") or {}
    loc = data.get("location") or {}
    scoring = data.get("scoring") or {}
    tailoring = data.get("tailoring") or {}

    home = _get(you, "home_country", str, "", "you.")
    if not home:
        raise SettingsError("you.home_country: required - the country you live in now")
    home_c = canonical_country(home)
    home_places = _strings(you, "home_places", "you.") or [home_c, *COUNTRY_ALIASES.get(home_c, ())]
    authorized = {home_c}
    for c in _strings(you, "authorized_countries", "you."):
        c = canonical_country(c)
        authorized |= {c, *COUNTRY_GROUPS.get(c, ())}
    regions = {r.strip().lower() for r in _strings(you, "regions", "you.")}

    on_home = _get(loc, "on_site_at_home", str, "ok", "location.")
    if on_home not in ("ok", "reject"):
        raise SettingsError(f"location.on_site_at_home: 'ok' or 'reject', got {on_home!r}")

    roles = [_role(r, i) for i, r in enumerate(data.get("roles") or [])]
    if not roles:
        raise SettingsError("roles: list at least one role family you are applying for")
    if len({r.name for r in roles}) != len(roles):
        raise SettingsError("roles: two roles share a name")
    interests = [_interest(r, i) for i, r in enumerate(data.get("interests") or [])]
    known = {i.name for i in interests}
    for r in roles:
        for ref in (
            r.interest,
            r.waive_if_interest,
            r.internships if isinstance(r.internships, str) else "",
            *(rule.interest for rule in r.cv_rules),
        ):
            if ref and ref not in known:
                raise SettingsError(f"roles[{r.name}]: no interest named {ref!r}")

    bonuses = []
    for i, b in enumerate(data.get("description_bonus") or []):
        pat = phrases(_strings(b, "terms", ""), key=f"description_bonus[{i}].terms")
        if pat is not None:
            bonuses.append(Bonus(pat, _get(b, "points", int, 0, f"description_bonus[{i}].")))

    llm = data.get("llm") or {}
    if not isinstance(llm, dict):
        raise SettingsError("llm: expected an object")
    provider = llm.get("provider", "")
    if provider and provider not in LLM_PROVIDERS:
        raise SettingsError(f"llm.provider: one of {', '.join(LLM_PROVIDERS)}, got {provider!r}")

    s = Settings(
        path=path,
        home_country=home_c,
        home_places=phrases(home_places, key="you.home_places"),
        authorized=authorized,
        regions=regions,
        utc_offset=_get(you, "utc_offset", float, 0.0, "you."),
        years_experience=_get(you, "years_experience", int, 0, "you."),
        needs_visa_sponsorship=_get(you, "needs_visa_sponsorship", bool, False, "you."),
        reject_onsite_at_home=on_home == "reject",
        targets=[_target(t, i) for i, t in enumerate(loc.get("relocation_targets") or [])],
        accept_locked={
            canonical_country(c) for c in _strings(loc, "accept_country_locks", "location.")
        },
        max_timezone_gap=_get(loc, "max_timezone_gap_hours", float, 4.0, "location."),
        preferred_regions=phrases(
            _strings(loc, "preferred_regions", "location."), key="location.preferred_regions"
        ),
        roles=roles,
        exclude_titles=phrases(_strings(data, "exclude_titles", ""), key="exclude_titles"),
        exclude_departments=phrases(
            _strings(data, "exclude_departments", ""), key="exclude_departments"
        ),
        interests=interests,
        description_bonus=bonuses,
        search_queries=_strings(data, "search_queries", ""),
        min_score=_get(scoring, "min_score", int, 45, "scoring."),
        max_per_company=_get(scoring, "max_per_company", int, 3, "scoring."),
        skills=_skill_map(tailoring.get("skills"), "skills"),
        gaps=_skill_map(tailoring.get("gaps"), "gaps"),
        weak_skills=set(_strings(tailoring, "weak", "tailoring.")),
        llm=llm,
    )
    _derive(s)
    return s


def _derive(s: Settings) -> None:
    s.open_entries = {s.home_country, *s.authorized, *s.regions, *OPEN_WORDS}
    s.auth_locks = tuple(
        p
        for country, ps in COUNTRY_LOCK_PHRASES.items()
        if country not in s.authorized and country not in s.accept_locked
        for p in ps
    ) + tuple(GENERIC_LOCK_PHRASES)
    s.onsite_locks = ONSITE_LOCK_PHRASES
    # Any name of a country you may work in: "must be authorized to work in the US" is a
    # lock for most people and the opposite for an American.
    names = {n for c in s.authorized for n in (c, *COUNTRY_ALIASES.get(c, ()))}
    s.authorized_mention = phrases(sorted(names), key="you.authorized_countries")
    frags = [pat for lets_in, pat in _REGION_LOCK_FRAGMENTS if not lets_in & s.regions]
    s.region_lock = re.compile("|".join(frags), re.I) if frags else None
    places = [t.places.pattern for t in s.targets]
    s.relocation = re.compile("|".join(places), re.I) if places else None
    s.open_signals = (
        "visa sponsorship",
        "we sponsor",
        "relocation support",
        "relocation package",
        "any time zone",
        "anywhere",
        "worldwide",
        "global",
        *sorted(s.regions),
    )


# -- loading ---------------------------------------------------------------

_cache: dict[Path, tuple[float, Settings]] = {}


def settings_path() -> Path:
    """Which file `current()` reads: $JOBHUNT_SETTINGS, then yours, then the example."""
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env)
    return SETTINGS_PATH if SETTINGS_PATH.exists() else EXAMPLE_PATH


def load(path: Path) -> Settings:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SettingsError(f"{path}: not found") from None
    except json.JSONDecodeError as exc:
        raise SettingsError(f"{path.name}: not valid JSON - {exc}") from None
    try:
        return from_dict(data, path)
    except SettingsError as exc:
        raise SettingsError(f"{path.name}: {exc}") from None


def current() -> Settings:
    """The active settings, reloaded whenever the file changes on disk."""
    path = settings_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    s = load(path)
    _cache[path] = (mtime, s)
    return s
