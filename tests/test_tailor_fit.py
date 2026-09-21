"""fit(): the keyword-trimming loop, driven by a fake PDF step so it runs without Word."""

from __future__ import annotations

from jobs import tailor as tl


def tailor(matched: int = 12) -> dict:
    return {
        "company": "Acme",
        "job_title": "QA Engineer",
        "variant": "qa_gaming",
        "matched": [f"k{i}" for i in range(matched)],
        "gaps": [],
        "slug": "acme-qa-engineer",
        "url": "https://x/1",
    }


def fake_pdf(monkeypatch, pages_by_keywords, engine="word"):
    """to_pdf stand-in: page count depends on how many keywords the CV was built with."""
    calls: list[int] = []

    def to_pdf(
        paths, *, keep_docx=False, pages_out=None, warn=True, warnings=None, engines_out=None
    ):
        assert keep_docx, "tailored CVs must keep their .docx"
        made = []
        for src in paths:
            pdf = src.with_suffix(".pdf")
            pdf.write_bytes(b"%PDF")
            n = calls[-1]
            if pages_out is not None and pages_by_keywords(n) is not None:
                pages_out[pdf] = pages_by_keywords(n)
            if engines_out is not None:
                engines_out[pdf] = engine
            made.append(pdf)
        return made

    real_build = tl.cvbuild.build_variant

    def build_variant(profile, tag, tailor=None, warnings=None):
        calls.append(len(tailor["matched"]))
        return real_build(profile, tag, tailor=tailor, warnings=warnings)

    monkeypatch.setattr(tl.cvbuild, "to_pdf", to_pdf)
    monkeypatch.setattr(tl.cvbuild, "build_variant", build_variant)
    return calls


def test_fit_keeps_every_keyword_when_it_fits(cv_sandbox, monkeypatch):
    fake_pdf(monkeypatch, lambda n: 2)
    r = tl.fit(tailor(12))
    assert (r.keywords_wanted, r.keywords_kept, r.pages, r.engine) == (12, 12, 2, "word")
    assert r.docx.suffix == ".docx" and r.docx.exists()
    assert r.pdf.suffix == ".pdf" and r.pdf.exists()
    assert r.error == ""


def test_fit_trims_until_two_pages_and_reports_each_rebuild(cv_sandbox, monkeypatch):
    calls = fake_pdf(monkeypatch, lambda n: 3 if n > 6 else 2)
    events = []
    r = tl.fit(tailor(12), report=events.append)
    assert calls == [12, 9, 6]
    assert (r.keywords_kept, r.pages) == (6, 2)
    assert [e.detail for e in events] == [True, True]
    assert events[0].message == "trimmed to 9 keywords, rebuilding"


def test_fit_drops_to_zero_keywords_on_a_variant_with_no_slack(cv_sandbox, monkeypatch):
    fake_pdf(monkeypatch, lambda n: 3 if n else 2)
    r = tl.fit(tailor(5))
    assert (r.keywords_kept, r.pages) == (0, 2)


def test_fit_without_a_pdf_engine_keeps_the_docx_and_leaves_pages_unchecked(cv_sandbox):
    r = tl.fit(tailor(4))  # cv_sandbox reports Word and LibreOffice missing
    assert r.docx.exists() and r.pdf is None
    assert (r.engine, r.pages, r.keywords_kept) == (None, None, 4)


def test_fit_stops_when_the_page_count_is_undetectable(cv_sandbox, monkeypatch):
    calls = fake_pdf(monkeypatch, lambda n: None, engine="libreoffice")
    r = tl.fit(tailor(12))
    assert calls == [12]
    assert (r.engine, r.pages, r.pdf is not None) == ("libreoffice", None, True)


def test_build_variant_collects_its_warning_instead_of_printing(cv_sandbox, capsys):
    profile = tl._profile()
    role = next(r for r in profile["roles"] if r["id"] == "current_role")  # qa_gaming lists it
    role["bullets"] = [b for b in role["bullets"] if "qa_gaming" not in b["tags"]]
    warnings: list[str] = []
    tl.cvbuild.build_variant(profile, "qa_gaming", warnings=warnings)
    assert capsys.readouterr().err == ""
    assert any("current_role" in w and "MISSING from the timeline" in w for w in warnings)


def test_to_pdf_reports_the_missing_engines_into_the_list(cv_sandbox, tmp_path, capsys):
    docx = tmp_path / "a.docx"
    docx.write_bytes(b"PK")
    warnings: list[str] = []
    assert tl.cvbuild.to_pdf([docx], warnings=warnings) == []
    assert capsys.readouterr().err == ""
    assert warnings == ["! neither Word nor LibreOffice is available - skipping PDF step"]
