# CV writing rules

Copy this to `cv/rules.md` and rewrite it as your own. That file is gitignored.

`profile.json` holds the facts; this file holds the rules for how they may be written. The
`/jobhunt` loop reads it before tailoring a CV or drafting an application answer, so
anything you want held to across every variant belongs here rather than in one bullet.

---

## Content

Rules about what may be claimed. Some that are worth considering:

- **Metrics you cannot defend.** A number on a CV is an invitation to be asked how it was
  measured. Decide once whether you want that conversation, and write the rule down either
  way.
- **Version numbers and product names.** Easy to get subtly wrong, and a wrong one reads as
  invention rather than error — especially on a CV targeting the field it names.
- **Work that did not succeed.** Often your most interesting material, and it survives
  cross-examination in a way an inflated success claim does not. Write it up as evaluation
  and diagnosis rather than deleting it or overstating it.

## Layout

- **A hard page limit**, if you want one. State the number.
- **Never end a page on a bare heading or job title** — a heading with nothing under it
  costs the reader more than the line it saves.

## Why

State the principle behind the rules, not just the rules. When a new situation comes up
that the list does not cover — and it will, on the first unusual posting — the principle is
what gets applied.

## How it is enforced

Note which rules are checked in code and which are on you. `cv/build.py` handles the
layout ones: `w:keepNext` / `w:keepLines` on headings and job titles, and `to_pdf()` reads
the page count back from Word and refuses to exceed `MAX_PAGES`.

Content rules are not enforceable in code. They hold only because they are written here.
