"""Regression tests for the source fetchers and their shared parsing helpers.

Every case here corresponds to a bug that actually cost us a board.
"""

from __future__ import annotations

import gzip
import json
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobs import sources  # noqa: E402
from jobs.sources import _hn_header, _hn_parse, _inflate, _iso, _repair  # noqa: E402


def test_iso_reads_second_epochs():
    assert _iso(1770824464) == "2026-02-11"


def test_iso_reads_millisecond_epochs():
    """Lever's createdAt is in ms. Treated as seconds it lands in the year 58000, which
    raises OSError on Windows and took the whole board down with it — every Lever board
    with at least one posting fetched empty."""
    assert _iso(1770824464173) == "2026-02-11"


def test_iso_agrees_across_both_units():
    assert _iso(1770824464173) == _iso(1770824464)


def test_iso_passes_through_string_dates():
    assert _iso("2026-08-06T12:00:00Z") == "2026-08-06"


def test_iso_is_empty_for_missing_values():
    assert _iso(None) == ""
    assert _iso(0) == ""
    assert _iso("") == ""


def test_iso_survives_an_unusable_number():
    """A single junk date must not cost us the rest of the board."""
    assert _iso(float("inf")) == ""
    assert _iso(-1e300) == ""


# --------------------------------------------------- truncated-body salvage


def test_repair_closes_a_wrapped_board():
    """Greenhouse and Ashby wrap their array in an object. The old salvage appended a
    bare `]`, never the `}` that closes the wrapper, so every partial was discarded and
    the board fetched empty. On 2026-09-09 that was six boards in one scrape — Anthropic,
    OpenAI, Scale AI, Notion, Elastic and Harvey — each several MB short of its own
    Content-Length, and each thrown away whole."""
    raw = b'{"jobs":[{"id":1,"title":"a"},{"id":2,"title":"b"},{"id":3,"tit'
    assert _repair(raw) == {"jobs": [{"id": 1, "title": "a"}, {"id": 2, "title": "b"}]}


def test_repair_closes_a_bare_array():
    """Lever's board is a top-level array. This shape already worked; keep it working."""
    raw = b'[{"id":1},{"id":2},{"id":3,"te'
    assert _repair(raw) == [{"id": 1}, {"id": 2}]


def test_repair_keeps_nested_objects_whole():
    """A record only counts as complete when its own braces have closed, not when any
    brace has — otherwise a posting is salvaged with half its `location` missing."""
    raw = b'[{"id":1,"location":{"name":"Berlin"}},{"id":2,"location":{"na'
    assert _repair(raw) == [{"id": 1, "location": {"name": "Berlin"}}]


def test_repair_ignores_delimiters_inside_strings():
    """Job descriptions are HTML and routinely contain braces and brackets. Counting one
    as a real delimiter shifts the depth and closes the document in the wrong place."""
    raw = b'{"jobs":[{"content":"a [bracket] and a {brace}"},{"content":"cut'
    assert _repair(raw) == {"jobs": [{"content": "a [bracket] and a {brace}"}]}


def test_repair_ignores_an_escaped_quote():
    """An escaped quote does not end the string. Treating it as the end puts the scanner
    outside a string for the rest of the body, and every brace after it miscounts."""
    raw = b'{"jobs":[{"content":"he said \\"hi\\" {"}, {"content":"cut'
    assert _repair(raw) == {"jobs": [{"content": 'he said "hi" {'}]}


def test_repair_handles_utf8_payloads():
    """exmox posts as `Senior Data Analyst (f/m/x)` with a non-ASCII dash. Scanning bytes
    is only safe because UTF-8 continuation bytes never collide with an ASCII delimiter."""
    raw = '{"jobs":[{"title":"Analyst — Hamburg"},{"title":"cut'.encode()
    assert _repair(raw) == {"jobs": [{"title": "Analyst — Hamburg"}]}


def test_repair_gives_up_before_the_first_complete_record():
    """Nothing whole to keep. Returning a half-record would be worse than failing."""
    assert _repair(b'{"jobs":[{"id":1,"tit') is None


def test_repair_gives_up_on_a_body_that_is_not_json():
    """An HTML error page served with a JSON content type must not salvage into junk."""
    assert _repair(b"<html><body>502 Bad Gateway</body></html>") is None


def test_repair_returns_none_for_an_empty_body():
    assert _repair(b"") is None


# ------------------------------------------------------- compressed transfer

BOARD = b'{"jobs":[{"id":1,"title":"a"},{"id":2,"title":"b"},{"id":3,"title":"c"}]}'


def test_inflate_reads_gzip():
    """The scrape sent `Accept-Encoding: identity` until 2026-09-09, which made the six
    largest boards stream 7-13 MB uncompressed and truncate every time. urllib does not
    decompress for us, so asking for gzip means we have to inflate it ourselves."""
    assert _inflate(gzip.compress(BOARD), "gzip") == BOARD


def test_inflate_reads_deflate():
    assert _inflate(zlib.compress(BOARD), "deflate") == BOARD


def test_inflate_reads_headerless_deflate():
    """Some servers label raw deflate as `deflate`, without the zlib header."""
    obj = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    assert _inflate(obj.compress(BOARD) + obj.flush(), "deflate") == BOARD


def test_inflate_is_case_insensitive():
    assert _inflate(gzip.compress(BOARD), "GZip") == BOARD


def test_inflate_passes_identity_through():
    assert _inflate(BOARD, "") == BOARD
    assert _inflate(BOARD, "identity") == BOARD


def test_inflate_passes_an_unknown_encoding_through():
    """br would need a non-stdlib decoder, so it is never advertised. If a server sends
    it anyway, hand the bytes on rather than corrupting them."""
    assert _inflate(BOARD, "br") == BOARD


def test_inflate_recovers_a_truncated_gzip_stream():
    """The whole point of inflating incrementally. `gzip.decompress` raises on a partial
    stream, which would lose a multi-MB fetch that `_repair` could still salvage."""
    partial = gzip.compress(BOARD)[:-40]
    out = _inflate(partial, "gzip")
    assert out and BOARD.startswith(out)


def test_a_truncated_gzip_board_still_salvages():
    """The two halves together, on a body big enough to cut like a real one: a compressed
    board truncated in transit still yields every record that arrived whole."""
    jobs = [{"id": i, "content": "description text " * 20} for i in range(200)]
    board = json.dumps({"jobs": jobs}).encode()
    partial = gzip.compress(board)[: -len(gzip.compress(board)) // 4]  # lose the last 25%
    recovered = _repair(_inflate(partial, "gzip"))
    assert recovered is not None
    assert 0 < len(recovered["jobs"]) < 200
    assert recovered["jobs"][0] == {"id": 0, "content": "description text " * 20}


def test_inflate_returns_bytes_on_undecodable_data():
    """A gzip label on something that is not gzip must not raise out of _read."""
    assert _inflate(b"not actually compressed", "gzip") == b"not actually compressed"


# ------------------------------------------------- Hacker News "Who is hiring?"


def test_hn_header_survives_a_company_with_a_domain_name():
    """The header used to be taken as `text.split(".")[0]`, which cuts at the first period.
    Every company whose name carries a domain lost its own header: `Modash.io | Senior
    Product Engineer | Remote (Europe)` became just `Modash`, taking the pipes with it, and
    the posting ended up with its company name as its title. All 248 HN postings on
    2026-09-09 had location == title, and the source had never produced a single lead."""
    raw = "Modash.io | Senior Product Engineer | Remote (Europe) | Full-time<p>Modash helps."
    assert _hn_header(raw) == "Modash.io | Senior Product Engineer | Remote (Europe) | Full-time"


def test_hn_header_stops_at_the_first_paragraph():
    assert _hn_header("A | B | C<p>body text here</p><p>more</p>") == "A | B | C"


def test_hn_header_handles_a_br_separator():
    assert _hn_header("A | B<br />body") == "A | B"


def test_hn_parse_splits_the_standard_header():
    assert _hn_parse("Modash.io | Senior Product Engineer | Remote (Europe) | Full-time") == (
        "Modash.io",
        "Senior Product Engineer",
        "Remote (Europe)",
    )


def test_hn_parse_drops_type_salary_and_link_from_the_location():
    """Fields after the role arrive in no fixed order, so they are classified, not indexed."""
    header = (
        "Quill | Fullstack SWE | Full-time | Remote, PT/ET | $150 - 210K + equity | https://q.co"
    )
    assert _hn_parse(header) == ("Quill", "Fullstack SWE", "Remote, PT/ET")


def test_hn_parse_strips_a_url_from_the_company():
    company, title, _ = _hn_parse("Smarkets ( https://www.smarkets.com ) | Data Engineer | London")
    assert company == "Smarkets"
    assert title == "Data Engineer"


def test_hn_parse_keeps_every_location_fragment():
    header = "Acme | ML Engineer | Utrecht, The Netherlands | HYBRID | DUTCH REQUIRED"
    _, _, where = _hn_parse(header)
    assert where == "Utrecht, The Netherlands; HYBRID; DUTCH REQUIRED"


def test_hn_parse_rejects_a_header_with_no_role():
    """`Smarkets | Full Time | Hybrid - Onsite (London, UK)` names no role. Inventing one
    from the location field is worse than dropping the posting - it cannot be scored."""
    assert _hn_parse("Smarkets | Full Time | Hybrid - Onsite (London, UK)") is None


def test_hn_parse_rejects_a_location_in_the_role_slot():
    assert _hn_parse("yeet | Chicago, IL / Remote | Full-Time") is None
    assert _hn_parse("Shepherd | ONSITE | San Francisco, CA") is None
    assert _hn_parse("Acme | Remote (Europe) | Full-time") is None


def test_hn_parse_rejects_a_header_with_no_pipes():
    assert _hn_parse("We are hiring engineers, get in touch") is None


# ------------------------------------------------- Workable / SmartRecruiters


def _serve(monkeypatch, routes: dict):
    """Answer sources._json from a URL -> body map; an Exception value is raised."""

    def fake(url):
        body = routes[url]
        if isinstance(body, Exception):
            raise body
        return body

    monkeypatch.setattr(sources, "_json", fake)


ASHBY = "https://api.ashbyhq.com/posting-api/job-board/va4u"


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        ({"location": "Philippines", "isRemote": True}, "Remote - Philippines"),
        ({"location": "Berlin", "workplaceType": "Remote"}, "Remote - Berlin"),
        ({"location": "Remote (EU)", "isRemote": True}, "Remote (EU)"),
        ({"location": "Madrid", "isRemote": False}, "Madrid"),
    ],
)
def test_ashby_marks_remote_postings_remote(monkeypatch, job, expected):
    """Ashby keeps remoteness in `isRemote`; VA4U's remote AI Specialist came through as
    plain "Philippines" and was scored as on-site abroad."""
    _serve(monkeypatch, {ASHBY: {"jobs": [{"title": "AI Specialist", **job}]}})
    [p] = sources.ashby("va4u")
    assert p.location == expected


def test_himalayas_keeps_recent_roles_once_with_their_hiring_countries(monkeypatch):
    now = datetime(2026, 9, 17, tzinfo=timezone.utc)
    fresh, stale = now.timestamp() - 86400, now.timestamp() - 40 * 86400
    job = {
        "title": "AI Automation Specialist",
        "companyName": "Bamboo Works",
        "applicationLink": "https://himalayas.app/companies/bamboo/jobs/ai",
        "pubDate": str(int(fresh)),
        "locationRestrictions": ["Philippines"],
        "description": "<p>Build n8n workflows</p>",
    }
    old = {**job, "applicationLink": "https://himalayas.app/old", "pubDate": str(int(stale))}
    url = "https://himalayas.app/jobs/api/search?q={}&page=1"
    _serve(
        monkeypatch,
        {url.format("ai%20automation"): {"jobs": [job, old]}, url.format("qa"): {"jobs": [job]}},
    )
    got = sources.himalayas(queries=("ai automation", "qa"), pages=1, now=now)
    assert len(got) == 1  # the stale one dropped, the repeat across queries collapsed
    [p] = got
    assert (p.company, p.location, p.countries) == (
        "Bamboo Works",
        "Remote - Philippines",
        ["Philippines"],
    )
    assert p.description == "Build n8n workflows"


def test_himalayas_raises_only_when_every_query_failed(monkeypatch):
    url = "https://himalayas.app/jobs/api/search?q=qa&page=1"
    _serve(monkeypatch, {url: OSError("down")})
    with pytest.raises(OSError):
        sources.himalayas(queries=("qa",), pages=1)


def test_jobicy_reads_the_hiring_geography(monkeypatch):
    job = {
        "url": "https://jobicy.com/jobs/1-qa",
        "jobTitle": "QA &amp; Automation Engineer",
        "companyName": "Upgrade",
        "jobGeo": "Argentina, Brazil",
        "jobDescription": "<p>Test things</p>",
        "pubDate": "2026-09-16T13:47:11+00:00",
        "jobIndustry": ["QA"],
    }
    _serve(monkeypatch, {"https://jobicy.com/api/v2/remote-jobs?count=100": {"jobs": [job]}})
    [p] = sources.jobicy()
    assert p.title == "QA & Automation Engineer"
    assert p.countries == ["Argentina", "Brazil"]
    assert p.posted_at == "2026-09-16"


WORKABLE = "https://apply.workable.com/api/v1/widget/accounts/foodics?details=true"
SR = "https://api.smartrecruiters.com/v1/companies/almosafer/postings"


def test_workable_reads_a_board(monkeypatch):
    job = {
        "title": " Associate AI Quality Engineer ",
        "url": "https://apply.workable.com/j/4EDB5A3F05",
        "city": "Riyadh",
        "country": "Saudi Arabia",
        "telecommuting": False,
        "description": "<p>Build <b>eval</b> infrastructure</p>",
        "published_on": "2026-09-10",
        "department": "Engineering",
    }
    _serve(monkeypatch, {WORKABLE: {"name": "Foodics", "jobs": [job]}})
    [p] = sources.workable("foodics")
    assert (p.source, p.company, p.title) == (
        "workable:foodics",
        "Foodics",
        "Associate AI Quality Engineer",
    )
    assert p.location == "Riyadh, Saudi Arabia"
    assert p.description == "Build eval infrastructure"
    assert p.posted_at == "2026-09-10"


def test_workable_marks_a_telecommuting_role_remote(monkeypatch):
    job = {"title": "QA Engineer", "city": "", "country": "UAE", "telecommuting": True}
    _serve(monkeypatch, {WORKABLE: {"jobs": [job]}})
    assert sources.workable("foodics")[0].location == "Remote - UAE"


def test_smartrecruiters_pages_and_reads_each_description(monkeypatch):
    first = {"totalFound": 2, "content": [{"id": "1", "name": "Senior QA Engineer - Backend"}]}
    second = {"totalFound": 2, "content": [{"id": "2", "name": "Data Analyst"}]}
    detail = {
        "postingUrl": "https://jobs.smartrecruiters.com/AlMosafer/1-senior-qa",
        "jobAd": {
            "sections": {
                "companyDescription": {"text": "We are a travel company."},
                "jobDescription": {"text": "<p>Test our APIs.</p>"},
                "qualifications": {"text": "Rest Assured"},
            }
        },
    }
    _serve(
        monkeypatch,
        {
            f"{SR}?limit=100&offset=0": first,
            f"{SR}?limit=100&offset=1": second,
            f"{SR}/1": detail,
            f"{SR}/2": {},
        },
    )
    qa, da = sources.smartrecruiters("almosafer")
    assert (qa.title, da.title) == ("Senior QA Engineer - Backend", "Data Analyst")
    assert qa.url == "https://jobs.smartrecruiters.com/AlMosafer/1-senior-qa"
    assert da.url == "https://jobs.smartrecruiters.com/almosafer/2"


def test_smartrecruiters_puts_the_job_before_the_company_blurb(monkeypatch):
    """score() reads eligibility from the first 1,200 characters of a description."""
    detail = {
        "jobAd": {
            "sections": {
                "companyDescription": {"text": "About us."},
                "jobDescription": {"text": "Saudi nationals only."},
            }
        }
    }
    listing = {"totalFound": 1, "content": [{"id": "1", "name": "QA Engineer"}]}
    _serve(monkeypatch, {f"{SR}?limit=100&offset=0": listing, f"{SR}/1": detail})
    [p] = sources.smartrecruiters("almosafer")
    assert p.description == "Saudi nationals only. About us."


def test_smartrecruiters_spells_the_country_out(monkeypatch):
    """`country` is an ISO code - score() would never read "sa" as Saudi Arabia."""
    loc = {"city": "Riyadh", "country": "sa", "fullLocation": "Riyadh, , Saudi Arabia"}
    listing = {"totalFound": 1, "content": [{"id": "1", "name": "QA", "location": loc}]}
    _serve(monkeypatch, {f"{SR}?limit=100&offset=0": listing, f"{SR}/1": {}})
    assert sources.smartrecruiters("almosafer")[0].location == "Riyadh, Saudi Arabia"


def test_smartrecruiters_survives_one_failing_description(monkeypatch):
    listing = {
        "totalFound": 2,
        "content": [{"id": "1", "name": "QA Engineer"}, {"id": "2", "name": "SDET"}],
    }
    _serve(
        monkeypatch,
        {
            f"{SR}?limit=100&offset=0": listing,
            f"{SR}/1": TimeoutError("read timed out"),
            f"{SR}/2": {"jobAd": {"sections": {"jobDescription": {"text": "Automate."}}}},
        },
    )
    broken, fine = sources.smartrecruiters("almosafer")
    assert (broken.title, broken.description) == ("QA Engineer", "")
    assert fine.description == "Automate."
