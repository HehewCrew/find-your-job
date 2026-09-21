"""Génère la version française du CV Automation QA.

    python cv/fr/build_fr.py              # -> .pdf (via Word, comme build.py)
    python cv/fr/build_fr.py --no-pdf     # .docx seulement
    python cv/fr/build_fr.py --keep-docx  # garde aussi le .docx

Volontairement autonome : le contenu vit dans fr/profile_fr.json, pas dans
profile.json. Seul le moteur de rendu (la classe Docx de build.py) est réutilisé,
pour que le CV français soit visuellement identique à l'anglais. Contrepartie
assumée : si un fait change dans profile.json, il faut le répercuter ici.

L'ordre des sections suit la convention française — Profil, Expériences,
Formation, Compétences — et se change dans `section_order` du JSON sans toucher
au code (par ex. remonter "competences" juste après "profil" pour la lecture ATS).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from build import Docx  # noqa: E402  (le sys.path doit être posé avant)

PROFILE_FR = HERE / "profile_fr.json"
OUT_DIR = ROOT / "out"
MAX_PAGES = 2


class DocxFR(Docx):
    """Identique au rendu anglais, mais le document est déclaré en fr-FR : Word
    corrige alors l'orthographe en français au lieu de souligner tout le texte."""

    def _styles_xml(self) -> str:
        xml = super()._styles_xml()
        return xml.replace(
            "</w:rPr></w:rPrDefault>",
            '<w:lang w:val="fr-FR" w:eastAsia="fr-FR" w:bidi="ar-SA"/></w:rPr></w:rPrDefault>',
            1,
        )


def render_entry(d: DocxFR, entry: dict, labels: dict, *, show_keywords: bool) -> None:
    end = entry.get("end") or labels["present"]
    d.para(
        d.run(entry["title"], bold=True) + d.run("\t") + d.run(f"{entry['start']} – {end}"),
        tabs=True,
        before=90,
        after=10,
        keep=True,
    )
    subtitle = " | ".join(x for x in (entry.get("org"), entry.get("location")) if x)
    if subtitle:
        d.para(d.run(subtitle, italic=True), keep=True)
    for text in entry.get("bullets", []):
        d.bullet(text)
    if show_keywords and entry.get("keywords"):
        d.para(
            d.run(labels["mots_cles"], bold=True, italic=True, size=d.size - 2)
            + d.run(", ".join(entry["keywords"]), italic=True, size=d.size - 2),
            before=10,
            after=30,
        )


def build(profile: dict) -> Path:
    meta, contact, links = profile["meta"], profile["contact"], profile["links"]
    labels = profile["labels"]
    d = DocxFR(meta, density=meta.get("density"))

    # En-tête
    d.para(
        d.run(contact["name"].upper(), bold=True, size=d.name_size, color=meta["accent"]),
        align="center",
        after=20,
    )
    d.para(d.run(profile["headline"], bold=True, size=d.size + 1), align="center", after=40)
    runs = d.run("  •  ".join([contact["phone"], contact["email"], contact["location"]]))
    for link in links.values():
        runs += d.run("  •  ") + d.hyperlink(link["label"], link["url"])
    d.para(runs, align="center", after=60)

    def section_profil() -> None:
        d.heading(labels["profil"])
        d.para(d.run(profile["profil"]), after=60)

    def section_competences() -> None:
        d.heading(labels["competences"])
        for group in profile["competences"]:
            d.para(d.run(group["title"], bold=True), before=60, after=10, keep=True)
            for item in group["items"]:
                d.bullet(item)

    def section_experience() -> None:
        d.heading(labels["experience"])
        for entry in profile["experience"]:
            render_entry(d, entry, labels, show_keywords=profile.get("show_keywords", True))

    def section_formation() -> None:
        d.heading(labels["formation"])
        for ed in profile["formation"]:
            d.para(
                d.run(ed["degree"], bold=True)
                + d.run("\t")
                + d.run(f"{ed['start']} – {ed['end']}"),
                tabs=True,
                before=50,
                after=10,
                keep=True,
            )
            d.para(d.run(ed["school"], italic=True))

    def section_certifications() -> None:
        certs = profile.get("certifications") or []
        d.heading(labels["certifications"] if certs else labels["langues_seules"])
        for cert in certs:
            d.bullet(cert)
        d.para(
            (d.run(labels["langues"], bold=True) if certs else "") + d.run(profile["langues"]),
            before=80 if certs else 0,
        )

    sections = {
        "profil": section_profil,
        "competences": section_competences,
        "experience": section_experience,
        "formation": section_formation,
        "certifications": section_certifications,
    }
    for key in profile["section_order"]:
        sections[key]()

    out = OUT_DIR / profile["output"]["folder"] / f"{profile['output']['filename']}.docx"
    d.save(out, title=f"{contact['name']} — {profile['headline']}", author=contact["name"])
    return out


def to_pdf(path: Path, *, keep_docx: bool) -> Path | None:
    """Word d'abord — le PDF est alors exactement ce que produit « Enregistrer au
    format PDF », comme pour les variantes anglaises. LibreOffice sert de secours
    (build hors Windows) : la mise en page peut différer de quelques points."""
    try:
        return _to_pdf_word(path, keep_docx=keep_docx)
    except ImportError:
        print("  · pywin32/Word indisponible — passage à LibreOffice", file=sys.stderr)
        return _to_pdf_soffice(path, keep_docx=keep_docx)


def _to_pdf_word(path: Path, *, keep_docx: bool) -> Path | None:
    import win32com.client as win32  # noqa: F401  (ImportError -> secours)
    from win32com.client import constants

    pdf = path.with_suffix(".pdf")
    word = win32.gencache.EnsureDispatch("Word.Application")
    word.Visible = False
    try:
        doc = word.Documents.Open(str(path))
        try:
            pages = doc.ComputeStatistics(2)  # wdStatisticPages
            if pages > MAX_PAGES:
                print(f"  !! {pdf.name} : {pages} pages (max {MAX_PAGES})", file=sys.stderr)
            doc.SaveAs(str(pdf), FileFormat=constants.wdFormatPDF)
        finally:
            doc.Close(False)
    finally:
        word.Quit()
    if not keep_docx:
        path.unlink(missing_ok=True)
    return pdf


def _to_pdf_soffice(path: Path, *, keep_docx: bool) -> Path | None:
    exe = "soffice"
    try:
        subprocess.run(
            [exe, "--headless", "--convert-to", "pdf", "--outdir", str(path.parent), str(path)],
            check=True,
            capture_output=True,
            timeout=180,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"  ! conversion PDF impossible ({exc}) — le .docx est conservé", file=sys.stderr)
        return None
    pdf = path.with_suffix(".pdf")
    if pdf.exists() and not keep_docx:
        # Le PDF est déjà écrit : un échec de suppression ne doit pas faire
        # échouer le build (montage en lecture seule, fichier ouvert dans Word...).
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            print(f"  · .docx non supprimé ({exc.strerror})", file=sys.stderr)
    return pdf if pdf.exists() else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Construit le CV Automation QA en français.")
    ap.add_argument("--no-pdf", action="store_true", help="s'arrêter au .docx")
    ap.add_argument("--keep-docx", action="store_true", help="garder le .docx à côté du PDF")
    args = ap.parse_args(argv)

    profile = json.loads(PROFILE_FR.read_text(encoding="utf-8"))
    docx = build(profile)
    print(f"  · {docx.relative_to(ROOT)}")
    if args.no_pdf:
        return 0
    pdf = to_pdf(docx, keep_docx=args.keep_docx)
    if pdf:
        print(f"  · {pdf.relative_to(ROOT)}")
    return 0 if pdf else 1


if __name__ == "__main__":
    raise SystemExit(main())
