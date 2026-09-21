"""Generate every CV variant as .pdf from a single profile.json.

Usage:
    python cv/build.py                  # build all variants -> pdf only
    python cv/build.py --keep-docx      # keep the intermediate .docx too
    python cv/build.py --no-pdf         # docx only (no Word needed)
    python cv/build.py sdet ai_qa       # build only the named variants
    python cv/build.py --list           # list available variants

The .docx is an intermediate: it is written with the standard library alone
(a .docx is a zip of XML parts), then handed to the installed Microsoft Word
via COM so the PDF is byte-for-byte what Word itself would export. Once the
PDF exists the .docx is deleted unless --keep-docx (or --no-pdf) is given.

If Word isn't installed or its COM automation fails, LibreOffice (headless
`soffice --convert-to pdf`) is tried as a fallback for whatever Word couldn't
convert. LibreOffice's page count isn't available the way Word's
ComputeStatistics is, so it's estimated by counting `/Type /Page` objects in
the PDF bytes - reliable for LibreOffice's default (uncompressed page tree)
export, but if the count comes back undetectable the caller is told to check
the page count by hand rather than told a wrong number.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
PROFILE = HERE / "profile.json"
OUT_DIR = HERE / "out"

# Page geometry, in twips (1 inch = 1440).
PAGE_W, PAGE_H = 12240, 15840
BULLET_INDENT = 284

NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)


def esc(text: str) -> str:
    return escape(str(text))


class Docx:
    """Accumulates paragraph XML and hyperlink relationships for one document."""

    def __init__(self, meta: dict, density: float | None = None):
        self.body: list[str] = []
        self.rels: list[tuple[str, str]] = []  # (rId, url)
        self.font = meta.get("font", "Calibri")
        self.size = int(meta.get("font_size_pt", 10)) * 2  # half-points
        self.name_size = int(meta.get("name_size_pt", 20)) * 2
        self.accent = meta.get("accent", "1F3864")
        self.margin = int(meta.get("margin_twips", 900))
        # Vertical rhythm, scaled by one knob so page count is easy to tune. A variant
        # with more content than the rest overrides it rather than tightening every CV.
        self.d = float(density if density is not None else meta.get("density", 1.0))

    def sp(self, twips: int) -> int:
        return int(twips * self.d)

    # -- relationships ---------------------------------------------------
    def link_id(self, url: str) -> str:
        rid = f"rIdLink{len(self.rels) + 1}"
        self.rels.append((rid, url))
        return rid

    # -- low-level runs --------------------------------------------------
    def _rpr(self, bold=False, italic=False, size=None, color=None, caps=False) -> str:
        parts = [f'<w:rFonts w:ascii="{self.font}" w:hAnsi="{self.font}" w:cs="{self.font}"/>']
        if bold:
            parts.append("<w:b/>")
        if italic:
            parts.append("<w:i/>")
        if caps:
            parts.append("<w:caps/>")
        if color:
            parts.append(f'<w:color w:val="{color}"/>')
        parts.append(f'<w:sz w:val="{size or self.size}"/>')
        parts.append(f'<w:szCs w:val="{size or self.size}"/>')
        return "<w:rPr>" + "".join(parts) + "</w:rPr>"

    def run(self, text: str, **kw) -> str:
        return f'<w:r>{self._rpr(**kw)}<w:t xml:space="preserve">{esc(text)}</w:t></w:r>'

    def hyperlink(self, label: str, url: str, **kw) -> str:
        rid = self.link_id(url)
        kw.setdefault("color", "0563C1")
        rpr = self._rpr(**kw).replace("<w:rPr>", '<w:rPr><w:u w:val="single"/>', 1)
        return (
            f'<w:hyperlink r:id="{rid}"><w:r>{rpr}'
            f'<w:t xml:space="preserve">{esc(label)}</w:t></w:r></w:hyperlink>'
        )

    # -- paragraphs ------------------------------------------------------
    def para(
        self,
        runs: str,
        *,
        align=None,
        before=0,
        after=40,
        tabs=False,
        indent=False,
        border=False,
        keep=False,
    ) -> None:
        p = ["<w:pPr>"]
        if keep:
            # keepNext binds this paragraph to the next one, so a heading or a job title
            # can never be left stranded as the last line on a page - Word pushes it over
            # to join its content. keepLines stops the paragraph splitting across pages.
            # Both must precede pBdr in the pPr schema order.
            p.append("<w:keepNext/><w:keepLines/>")
        if border:
            p.append(
                f'<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="2" '
                f'w:color="{self.accent}"/></w:pBdr>'
            )
        if tabs:
            right_tab = PAGE_W - (2 * self.margin)
            p.append(f'<w:tabs><w:tab w:val="right" w:pos="{right_tab}"/></w:tabs>')
        if indent:
            p.append(f'<w:ind w:left="{BULLET_INDENT}" w:hanging="{BULLET_INDENT}"/>')
        if align:
            p.append(f'<w:jc w:val="{align}"/>')
        p.append(
            f'<w:spacing w:before="{self.sp(before)}" w:after="{self.sp(after)}" '
            f'w:line="228" w:lineRule="auto"/>'
        )
        p.append("</w:pPr>")
        self.body.append("<w:p>" + "".join(p) + runs + "</w:p>")

    def heading(self, text: str) -> None:
        self.para(
            self.run(text, bold=True, caps=True, color=self.accent, size=self.size + 2),
            before=150,
            after=60,
            border=True,
            keep=True,
        )

    def bullet(self, text: str) -> None:
        self.para(self.run("•\t") + self.run(text), indent=True, after=15)

    def spacer(self, after=60) -> None:
        self.para("", after=after)

    # -- packaging -------------------------------------------------------
    def _document_xml(self) -> str:
        m = self.margin
        sect = (
            f'<w:sectPr><w:pgSz w:w="{PAGE_W}" w:h="{PAGE_H}"/>'
            f'<w:pgMar w:top="{m}" w:right="{m}" w:bottom="{m}" '
            f'w:left="{m}" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
        )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f"<w:document {NS}><w:body>{''.join(self.body)}{sect}</w:body></w:document>"
        )

    def _styles_xml(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f"<w:styles {NS}><w:docDefaults><w:rPrDefault><w:rPr>"
            f'<w:rFonts w:ascii="{self.font}" w:hAnsi="{self.font}" w:cs="{self.font}"/>'
            f'<w:sz w:val="{self.size}"/><w:szCs w:val="{self.size}"/>'
            "</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>"
            '<w:spacing w:after="0" w:line="240" w:lineRule="auto"/>'
            "</w:pPr></w:pPrDefault></w:docDefaults>"
            '<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/>'
            '<w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>'
            "</w:styles>"
        )

    def _rels_xml(self) -> str:
        base = (
            '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        )
        hyper = "".join(
            f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/relationships/hyperlink" Target="{esc(url)}" '
            f'TargetMode="External"/>'
            for rid, url in self.rels
        )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'relationships">{base}{hyper}</Relationships>'
        )

    def save(self, path: Path, title: str, author: str) -> None:
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package'
            '.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd'
            '.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd'
            '.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd'
            '.openxmlformats-package.core-properties+xml"/></Types>'
        )
        root_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats'
            '.org/officeDocument/2006/relationships/officeDocument" Target="word/'
            'document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats'
            '.org/package/2006/relationships/metadata/core-properties" Target="docProps/'
            'core.xml"/></Relationships>'
        )
        core = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
            'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:title>{esc(title)}</dc:title><dc:creator>{esc(author)}</dc:creator>"
            "</cp:coreProperties>"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", content_types)
            z.writestr("_rels/.rels", root_rels)
            z.writestr("docProps/core.xml", core)
            z.writestr("word/document.xml", self._document_xml())
            z.writestr("word/styles.xml", self._styles_xml())
            z.writestr("word/_rels/document.xml.rels", self._rels_xml())


# ---------------------------------------------------------------- rendering


def pick(entries: list[dict], tag: str) -> list[str]:
    """Bullets whose tag list includes this variant."""
    return [b["text"] for b in entries if tag in b.get("tags", [])]


def keywords_for(block: dict, tag: str) -> list[str]:
    kw = block.get("keywords", {})
    return kw.get(tag) or kw.get("default") or []


def _month_key(value: str) -> tuple[int, int]:
    """'04/2026' -> (2026, 4). Sorts dates without parsing them as real dates."""
    month, _, year = value.partition("/")
    return (int(year), int(month)) if year else (0, 0)


def merge_roles(specs: list[dict], by_id: dict, tag: str) -> list[dict]:
    """Collapse several roles at one employer into a single dated entry.

    Successive titles at the same company cost a heading, a subtitle and a keyword line
    each, which is most of a page on a long variant. The bullets still live once in
    `roles` - this only changes how they are grouped for one variant.

    The title is supplied per spec and must be real: list the actual titles held rather
    than inventing a unifying one, or the CV stops surviving a reference check.
    """
    merged = []
    for spec in specs:
        parts = [by_id[i] for i in spec["ids"] if i in by_id]
        if not parts:
            continue
        ordered = sorted(parts, key=lambda e: _month_key(e["start"]), reverse=True)
        bullets: list[dict] = []
        keywords: list[str] = []
        for part in ordered:
            bullets += part.get("bullets", [])
            keywords += keywords_for(part, tag)
        entry = {
            "id": spec.get("id", "+".join(spec["ids"])),
            "title": spec["title"],
            "org": spec.get("org", ordered[0].get("org", "")),
            "location": spec.get("location", ordered[0].get("location", "")),
            "start": ordered[-1]["start"],
            "bullets": bullets,
            "keywords": {"default": list(dict.fromkeys(keywords))},
        }
        # An entry still running has no end date; the merged span inherits that.
        if all(p.get("end") for p in ordered):
            entry["end"] = max((p["end"] for p in ordered), key=_month_key)
        merged.append(entry)
    return merged


def render_entry(d: Docx, entry: dict, tag: str, links: dict, *, show_keywords=True) -> None:
    bullets = pick(entry.get("bullets", []), tag)
    if not bullets:
        return
    title = entry.get("title_overrides", {}).get(tag, entry["title"])
    dates = entry["start"] + (f" – {entry['end']}" if entry.get("end") else "")
    d.para(
        d.run(title, bold=True) + d.run("\t") + d.run(dates),
        tabs=True,
        before=90,
        after=10,
        keep=True,
    )
    subtitle = " | ".join(x for x in (entry.get("org"), entry.get("location")) if x)
    if subtitle:
        d.para(d.run(subtitle, italic=True), keep=True)
    for text in bullets:
        d.bullet(text)
    for key in entry.get("link_list", []) or ([entry["link"]] if entry.get("link") else []):
        link = links[key]
        d.para(d.run("▸ ") + d.hyperlink(link["label"], link["url"]), indent=True, after=30)
    if show_keywords:
        kws = keywords_for(entry, tag)
        if kws:
            d.para(
                d.run("Keywords: ", bold=True, italic=True, size=d.size - 2)
                + d.run(", ".join(kws), italic=True, size=d.size - 2),
                before=10,
                after=30,
            )


def build_variant(
    profile: dict,
    tag: str,
    tailor: dict | None = None,
    warnings: list[str] | None = None,
    out_dir: Path | None = None,
) -> tuple[Path, str]:
    """Render one variant. `tailor` optionally targets it at a specific posting:
    {company, job_title, matched: [...], slug} - `matched` must only ever contain
    skills you actually have, so tailoring stays honest. `out_dir` replaces OUT_DIR, for a
    preview or a validation build that must not land among the real CVs."""
    try:
        v = profile["variants"][tag]
    except KeyError:
        # jobs/score.py picks a variant by name from a fixed set, so a profile missing one
        # crashes on the first posting of that family - and cv/init.py writes only the one
        # variant it interviewed you about. Say which one is missing rather than raising a
        # bare KeyError from inside the renderer.
        have = ", ".join(sorted(profile.get("variants", {}))) or "none"
        raise SystemExit(
            f"cv/profile.json has no '{tag}' variant, and a posting was scored into it.\n"
            f"Variants present: {have}\n"
            f"Add a '{tag}' block under \"variants\" - copy the shape from "
            f"cv/profile.example.json. Every variant name in jobs/score.py:pick_variant "
            f"must exist, even as a stub."
        ) from None
    meta, contact, links = profile["meta"], profile["contact"], profile["links"]
    d = Docx(meta, density=v.get("density"))

    # Header block
    d.para(
        d.run(contact["name"].upper(), bold=True, size=d.name_size, color=meta["accent"]),
        align="center",
        after=20,
    )
    d.para(d.run(v["headline"], bold=True, size=d.size + 1), align="center", after=40)

    bits = [contact["phone"], contact["email"], contact["location"]]
    if v.get("contact_extra"):
        bits.append(v["contact_extra"])
    runs = d.run("  •  ".join(bits))
    for key in v.get("links", []):
        runs += d.run("  •  ") + d.hyperlink(links[key]["label"], links[key]["url"])
    d.para(runs, align="center", after=60)

    # Summary
    d.heading("Professional Summary")
    d.para(
        d.run(
            v["summary"]
            .replace("{years}", meta["years_experience"])
            .replace("{ai_tools}", meta["ai_tools"])
        ),
        after=60,
    )

    # Skills. When tailored, the JD-matched skills lead - this is the ATS keyword
    # surface, and it lists only skills already present elsewhere in the profile.
    d.heading("Core Skills")
    if tailor and tailor.get("matched"):
        label = (
            f"Most Relevant to {tailor['company']}"
            if tailor.get("company")
            else "Most Relevant to This Role"
        )
        d.para(d.run(label, bold=True, color=d.accent), before=60, after=10, keep=True)
        d.bullet(" · ".join(tailor["matched"]))
    for key in v.get("skill_groups", []):
        group = profile["skill_groups"][key]
        d.para(d.run(group["title"], bold=True), before=60, after=10, keep=True)
        for item in group["items"]:
            d.bullet(item)

    by_id = {e["id"]: e for e in profile["roles"] + profile["creative"]}

    # Creative work (game designer CV puts this above employment)
    if v.get("creative"):
        d.heading(v.get("creative_title", "Creative & Design Work"))
        for rid in v["creative"]:
            render_entry(d, by_id[rid], tag, links, show_keywords=False)

    # Employment. A role listed for a variant but carrying no bullets tagged for it
    # would silently vanish and leave an unexplained gap in the timeline - warn loudly.
    d.heading(v.get("experience_title", "Work Experience"))
    show_keywords = v.get("show_keywords", True)
    if v.get("merge_roles"):
        entries = merge_roles(v["merge_roles"], by_id, tag)
    else:
        entries = [by_id[rid] for rid in v.get("roles", [])]
    for entry in entries:
        if entry.get("draft"):
            continue
        if not pick(entry.get("bullets", []), tag):
            _warn(
                f"  ! {tag}: role '{entry['id']}' has no bullets tagged '{tag}' - "
                f"it will be MISSING from the timeline",
                warnings,
            )
        render_entry(d, entry, tag, links, show_keywords=show_keywords)

    # Community
    if v.get("community"):
        d.heading(v.get("community_title", "Gaming Community Involvement"))
        for rid in v["community"]:
            render_entry(d, by_id[rid], tag, links, show_keywords=False)

    # Education
    d.heading("Education")
    for ed in profile["education"]:
        d.para(
            d.run(ed["degree"], bold=True) + d.run("\t") + d.run(f"{ed['start']} – {ed['end']}"),
            tabs=True,
            before=50,
            after=10,
            keep=True,
        )
        d.para(d.run(ed["school"], italic=True))

    # Certifications & languages. A variant can drop the certificates and keep the
    # languages - credential IDs earn their space on a QA CV, far less on a design one.
    certs = profile["certifications"] if v.get("show_certifications", True) else []
    d.heading("Certifications & Languages" if certs else "Languages")
    for cert in certs:
        d.bullet(cert)
    d.para(
        (d.run("Languages: ", bold=True) if certs else "") + d.run(profile["languages"]),
        before=80 if certs else 0,
    )

    # Optional closing pitch
    if v.get("closing"):
        d.heading(v.get("closing_title", "Why This Role"))
        d.para(d.run(v["closing"]))

    if tailor:
        # One subfolder per application, so every CV can carry the same name - the file
        # that gets attached to a form should read as a CV, not as a build artefact.
        # The posting is identified by the folder; `d.save` creates it.
        out = (out_dir or OUT_DIR) / "tailored" / tailor["slug"] / f"{v['filename']}.docx"
    else:
        out = (out_dir or OUT_DIR) / v["folder"] / f"{v['filename']}.docx"
    d.save(out, title=f"{contact['name']} — {v['headline']}", author=contact["name"])
    return out, v["headline"]


# ------------------------------------------------------------------- PDF


MAX_PAGES = 2
_WD_STATISTIC_PAGES = 2


def _warn(message: str, warnings: list[str] | None) -> None:
    """Print to stderr as before, or collect for a caller that reports it itself."""
    if warnings is None:
        print(message, file=sys.stderr)
    else:
        warnings.append(message.strip())


def _report_oversized(
    oversized: list[tuple[Path, int]], warn: bool, warnings: list[str] | None = None
) -> None:
    if not (oversized and warn):
        return
    _warn(
        f"\n  !! {len(oversized)} CV(s) OVER THE {MAX_PAGES}-PAGE LIMIT - do not send:",
        warnings,
    )
    for path, pages in oversized:
        _warn(f"     {pages} pages: {path.name}", warnings)
    _warn("     Fix by lowering meta.density in cv/profile.json, or cutting a bullet.", warnings)


def _word_to_pdf(
    paths: list[Path],
    *,
    keep_docx: bool,
    pages_out: dict[Path, int] | None,
    warn: bool,
    warnings: list[str] | None = None,
    engines_out: dict[Path, str] | None = None,
) -> list[Path]:
    """Convert .docx -> .pdf via Word COM. Returns what succeeded.

    Word is the only thing here that knows how the document actually lays out, so the
    two-page rule is checked while it has the file open - see `pages_out` on to_pdf().
    A source whose conversion failed is left on disk rather than deleted, so a Word
    hiccup on one file doesn't erase that variant's only output.
    """
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return []

    made: list[Path] = []
    oversized: list[tuple[Path, int]] = []
    # COM must be initialised per thread. The CLI runs on the main thread, where pywin32
    # already did it; a UI worker thread is not, and Word fails there without this.
    off_main = threading.current_thread() is not threading.main_thread()
    if off_main:
        pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        for src in paths:
            dst = src.with_suffix(".pdf")
            doc = word.Documents.Open(str(src), ReadOnly=True, AddToRecentFiles=False)
            try:
                doc.SaveAs2(str(dst), FileFormat=17)  # 17 = wdFormatPDF
                made.append(dst)
                if engines_out is not None:
                    engines_out[dst] = "word"
                pages = int(doc.ComputeStatistics(_WD_STATISTIC_PAGES))
                if pages_out is not None:
                    pages_out[dst] = pages
                if pages > MAX_PAGES:
                    oversized.append((dst, pages))
            finally:
                doc.Close(SaveChanges=0)
            if not keep_docx:
                with contextlib.suppress(OSError):
                    src.unlink()
    except Exception as exc:  # noqa: BLE001
        if warn:
            _warn(f"  ! Word PDF conversion failed: {exc}", warnings)
    finally:
        if word is not None:
            with contextlib.suppress(Exception):
                word.Quit()
        if off_main:
            pythoncom.CoUninitialize()
    _report_oversized(oversized, warn, warnings)
    return made


def _find_soffice() -> str | None:
    found = shutil.which("soffice") or shutil.which("soffice.exe")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        if Path(candidate).exists():
            return candidate
    return None


_PDF_PAGE_OBJECT = re.compile(rb"/Type\s*/Page(?!s)\b")


def _count_pdf_pages(pdf_path: Path) -> int | None:
    """Best-effort page count for a LibreOffice-produced PDF.

    Word gives page counts directly (ComputeStatistics); LibreOffice's CLI doesn't, so
    this counts `/Type /Page` objects instead - correct for LibreOffice's default,
    uncompressed page-tree export. Returns None rather than a guess if nothing matched
    (e.g. a compressed object stream), so callers know to check the page count by hand
    instead of trusting a count that might be wrong.
    """
    try:
        data = pdf_path.read_bytes()
    except OSError:
        return None
    count = len(_PDF_PAGE_OBJECT.findall(data))
    return count or None


def _libreoffice_to_pdf(
    paths: list[Path],
    *,
    soffice: str,
    keep_docx: bool,
    pages_out: dict[Path, int] | None,
    warn: bool,
    warnings: list[str] | None = None,
    engines_out: dict[Path, str] | None = None,
) -> list[Path]:
    """Convert .docx -> .pdf via headless LibreOffice. Returns what succeeded.

    Fallback for machines without Word - see to_pdf(). Page counts are estimated
    (see _count_pdf_pages); a source whose conversion failed is left on disk, same
    as the Word path.
    """
    made: list[Path] = []
    oversized: list[tuple[Path, int]] = []
    unknown_pages: list[Path] = []
    for src in paths:
        dst = src.with_suffix(".pdf")
        try:
            result = subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--norestore",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(src.parent),
                    str(src),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            if warn:
                _warn(f"  ! LibreOffice conversion failed for {src.name}: {exc}", warnings)
            continue
        if result.returncode != 0 or not dst.exists():
            if warn:
                detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
                _warn(f"  ! LibreOffice conversion failed for {src.name}: {detail}", warnings)
            continue
        made.append(dst)
        if engines_out is not None:
            engines_out[dst] = "libreoffice"
        pages = _count_pdf_pages(dst)
        if pages is None:
            unknown_pages.append(dst)
        else:
            if pages_out is not None:
                pages_out[dst] = pages
            if pages > MAX_PAGES:
                oversized.append((dst, pages))
        if not keep_docx:
            with contextlib.suppress(OSError):
                src.unlink()
    if unknown_pages and warn:
        names = ", ".join(p.name for p in unknown_pages)
        _warn(
            f"  ? page count undetectable (LibreOffice export) for: {names} - check by hand.",
            warnings,
        )
    _report_oversized(oversized, warn, warnings)
    return made


def to_pdf(
    paths: list[Path],
    *,
    keep_docx: bool = False,
    pages_out: dict[Path, int] | None = None,
    warn: bool = True,
    warnings: list[str] | None = None,
    engines_out: dict[Path, str] | None = None,
) -> list[Path]:
    """Convert .docx -> .pdf, preferring Word and falling back to LibreOffice.

    Word is tried first for every path. Word gives an exact page count via
    ComputeStatistics, which is why it's preferred - see `pages_out`. Whatever Word
    didn't convert (Word missing, COM unavailable, or a per-file failure that leaves
    the .docx on disk) is retried through headless LibreOffice if it's installed.

    `pages_out`, if given, is filled with {pdf path: page count} for whichever engine
    produced it. A caller that wants to react to an over-length CV (rather than just
    print the warning) has no other way to learn it - see jobs.paste, which drops
    keywords and rebuilds until it fits.

    `warn=False` silences the over-length report for callers doing exactly that: an
    intermediate attempt being too long is the mechanism working, not a fault, and
    printing the warning once per attempt buries the one result that matters.

    `warnings`, if given, collects every message instead of printing it. `engines_out` is
    filled with {pdf path: "word" | "libreoffice"}.
    """
    if not paths:
        return []
    made = _word_to_pdf(
        paths,
        keep_docx=keep_docx,
        pages_out=pages_out,
        warn=warn,
        warnings=warnings,
        engines_out=engines_out,
    )
    remaining = [p for p in paths if p.exists() and p.with_suffix(".pdf") not in made]
    if not remaining:
        return made
    soffice = _find_soffice()
    if soffice is None:
        if warn and not made:
            _warn("  ! neither Word nor LibreOffice is available - skipping PDF step", warnings)
        return made
    made += _libreoffice_to_pdf(
        remaining,
        soffice=soffice,
        keep_docx=keep_docx,
        pages_out=pages_out,
        warn=warn,
        warnings=warnings,
        engines_out=engines_out,
    )
    return made


# ------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build CV variants from profile.json")
    ap.add_argument("variants", nargs="*", help="variant keys to build (default: all)")
    ap.add_argument("--no-pdf", action="store_true", help="skip the Word PDF export, keep docx")
    ap.add_argument("--keep-docx", action="store_true", help="keep the intermediate .docx files")
    ap.add_argument("--list", action="store_true", help="list variant keys and exit")
    ap.add_argument("--clean", action="store_true", help="wipe cv/out before building")
    args = ap.parse_args(argv)

    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    available = list(profile["variants"])

    if args.list:
        for key in available:
            v = profile["variants"][key]
            print(f"  {key:<20} {v['headline']}")
        return 0

    wanted = args.variants or available
    unknown = [w for w in wanted if w not in profile["variants"]]
    if unknown:
        print(f"unknown variant(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"available: {', '.join(available)}", file=sys.stderr)
        return 2

    drafts = [r["id"] for r in profile["roles"] if r.get("draft")]
    if drafts:
        print(f"note: skipping draft role(s) not yet filled in: {', '.join(drafts)}\n")

    if args.clean and OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)

    keep_docx = args.keep_docx or args.no_pdf
    built: list[Path] = []
    for key in wanted:
        path, headline = build_variant(profile, key)
        built.append(path)
        if keep_docx:
            print(f"  docx  {path.relative_to(HERE.parent)}")

    if not args.no_pdf:
        made = to_pdf(built, keep_docx=args.keep_docx)
        for pdf in made:
            print(f"  pdf   {pdf.relative_to(HERE.parent)}")
        if len(made) < len(built):
            print(
                f"  ! {len(built) - len(made)} variant(s) produced no PDF - their .docx were kept",
                file=sys.stderr,
            )

    print(f"\n{len(built)} variant(s) built into {OUT_DIR.relative_to(HERE.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
