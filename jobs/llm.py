"""An optional second opinion on a pasted job description, from an LLM you bring.

The rules in score.py read a title, a location field and the top of a description. They
cannot read a JD the way a person does: the Norwegian-native tester that scored 121 on
2026-09-21 was obvious to a reader and invisible to a regex. This asks a model to read
the whole posting against your settings, your priorities and your CV, and say
apply / consider / skip - with reasons, and questions you can keep asking.

Off unless you turn it on. In settings.json:

    "llm": {"provider": "anthropic"}                      # key from $ANTHROPIC_API_KEY
    "llm": {"provider": "openai", "model": "<model id>"}  # key from $OPENAI_API_KEY
    "llm": {"provider": "openai-compatible",              # OpenRouter, Groq, Ollama...
            "base_url": "https://openrouter.ai/api/v1",
            "model": "<model id>", "api_key_env": "OPENROUTER_API_KEY"}

The key is only ever read from an environment variable, never from a file, so it cannot
be committed by accident. Every call sends the JD, your settings summary, priorities.md
and your CV's summary and skills to that provider, and costs a few cents.

Standard library only: raw HTTPS through urllib, so the repo keeps no runtime dependency.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import settings as st

ROOT = Path(__file__).resolve().parent.parent
PRIORITIES_PATH = ROOT / "jobs" / "priorities.md"
TIMEOUT = 180

ANTHROPIC_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_DEFAULT_MODEL = "claude-opus-5"
# On a policy decline the API re-runs the request on a fallback model it picks, inside the
# same call. Only sent for the model families that support it.
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FALLBACK_MODELS = re.compile(r"^claude-(opus-5|fable-5)")
OPENAI_URL = "https://api.openai.com/v1"

DECISIONS = ("apply", "consider", "skip")


class LLMError(RuntimeError):
    """The provider could not be reached, refused, or answered in a shape we cannot use."""


@dataclass
class Verdict:
    decision: str
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    source: str = "rules"


@dataclass
class Client:
    provider: str
    model: str
    api_key: str
    base_url: str = ""

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    @classmethod
    def from_settings(cls, s: st.Settings, env: dict | None = None) -> Client | None:
        """A ready client, or None when the LLM is not configured. Raises LLMError when it is
        configured but unusable, so a missing key is reported rather than silently skipped."""
        env = os.environ if env is None else env
        cfg = s.llm or {}
        provider = cfg.get("provider", "")
        if not provider:
            return None
        key_env = cfg.get("api_key_env") or st.LLM_PROVIDERS[provider]
        key = env.get(key_env, "")
        base = (cfg.get("base_url") or "").rstrip("/")
        model = cfg.get("model", "")
        if provider == "anthropic":
            model = model or ANTHROPIC_DEFAULT_MODEL
        elif not model:
            raise LLMError(f"llm.model is required for provider {provider!r}")
        if provider == "openai-compatible" and not base:
            raise LLMError("llm.base_url is required for provider 'openai-compatible'")
        # A local server (Ollama, LM Studio) usually takes no key at all.
        local = bool(re.match(r"https?://(localhost|127\.0\.0\.1)", base))
        if not key and not local:
            raise LLMError(f"llm is set to {provider!r} but ${key_env} is not set")
        return cls(provider=provider, model=model, api_key=key, base_url=base)

    # -- transport ---------------------------------------------------------------

    def request(self, system: str, messages: list[dict]) -> tuple[str, dict, bytes]:
        """(url, headers, body) for one call. Split out so it can be tested offline."""
        if self.provider == "anthropic":
            url = f"{self.base_url or ANTHROPIC_URL}/v1/messages"
            headers = {
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            }
            body: dict = {
                "model": self.model,
                "max_tokens": 16000,
                "system": system,
                "messages": messages,
            }
            if _FALLBACK_MODELS.match(self.model):
                headers["anthropic-beta"] = ANTHROPIC_FALLBACK_BETA
                body["fallbacks"] = "default"
        else:
            url = f"{self.base_url or OPENAI_URL}/chat/completions"
            headers = {"content-type": "application/json"}
            if self.api_key:
                headers["authorization"] = f"Bearer {self.api_key}"
            body = {
                "model": self.model,
                "messages": [{"role": "system", "content": system}, *messages],
            }
        return url, headers, json.dumps(body).encode("utf-8")

    def chat(self, system: str, messages: list[dict]) -> str:
        url, headers, body = self.request(system, messages)
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise LLMError(f"{self.provider} returned HTTP {exc.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"could not reach {self.provider}: {exc}") from None
        except json.JSONDecodeError:
            raise LLMError(f"{self.provider} answered with something that is not JSON") from None
        return self.text_of(data)

    def text_of(self, data: dict) -> str:
        if self.provider == "anthropic":
            if data.get("stop_reason") == "refusal":
                raise LLMError("the model declined to assess this posting")
            text = "".join(
                b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text"
            )
        else:
            choices = data.get("choices") or [{}]
            text = ((choices[0] or {}).get("message") or {}).get("content") or ""
        if not text.strip():
            raise LLMError(f"{self.provider} returned an empty answer")
        return text


# -- the assessment ------------------------------------------------------------------

SYSTEM = """You screen job postings for one job seeker. You are given their search \
settings, their own notes on what they want, a summary of their CV, what a rule-based \
filter concluded, and the full job description.

Judge whether they should spend an application on this posting. Look hardest for \
blockers a keyword filter misses: work authorization or residency requirements, \
required languages, relocation to somewhere they did not name, timezone or working-hour \
floors, seniority far above theirs, hard requirements their CV plainly lacks. Quote or \
closely paraphrase the posting when you cite one. When the posting does not say, say so \
rather than guessing.

Answer with one JSON object and nothing else:
{"decision": "apply" | "consider" | "skip",
 "reasons": [short strings, the case for the decision],
 "blockers": [short strings, each a hard reason they cannot or should not apply],
 "gaps": [requirements their CV does not show, that they should prepare for],
 "questions": [what to ask the employer or check on the form before applying]}

"skip" means a blocker. "consider" means no blocker, but a real doubt. After that first \
answer the user may ask follow-up questions about the posting; answer those in plain text."""


def describe_settings(s: st.Settings) -> str:
    targets = ", ".join(t.country.title() for t in s.targets) or "none"
    roles = "; ".join(
        f"{r.name} (CV '{r.cv}', up to {r.max_level}"
        + (", internships ok" if r.internships else "")
        + ")"
        for r in s.roles
    )
    return "\n".join(
        [
            f"- Lives in: {s.home_country.title()} (UTC{s.utc_offset:+g})",
            f"- Can work without sponsorship in: {', '.join(sorted(s.authorized))}",
            f"- Regions they belong to: {', '.join(sorted(s.regions)) or 'none stated'}",
            f"- Would relocate to: {targets}",
            "- On-site where they live: "
            + ("no - they are leaving" if s.reject_onsite_at_home else "fine"),
            f"- Needs visa sponsorship abroad: {'yes' if s.needs_visa_sponsorship else 'no'}",
            f"- Years of experience: {s.years_experience or 'not stated'}",
            f"- Role families, best first: {roles}",
            f"- Interests: {', '.join(i.name for i in s.interests) or 'none'}",
        ]
    )


def describe_cv(profile: dict, variant: str) -> str:
    v = (profile.get("variants") or {}).get(variant) or {}
    meta = profile.get("meta") or {}
    summary = (v.get("summary") or "").replace("{years}", str(meta.get("years_experience", "")))
    summary = summary.replace("{ai_tools}", str(meta.get("ai_tools", "")))
    groups = profile.get("skill_groups") or {}
    skills = [
        item
        for gid in v.get("skill_groups") or groups
        for item in (groups.get(gid) or {}).get("items", [])
        if isinstance(groups.get(gid), dict)
    ]
    roles = [
        f"{r.get('title', '')} at {r.get('org', '')} ({r.get('start', '')}-{r.get('end') or 'now'})"
        for r in profile.get("roles") or []
    ]
    return "\n".join(
        [
            f"Headline: {v.get('headline', '')}",
            f"Summary: {summary}",
            f"Experience: {'; '.join(roles) or 'none listed'}",
            f"Skills: {', '.join(skills) or 'none listed'}",
            f"Languages: {profile.get('languages', '')}",
        ]
    )


def priorities_text(path: Path = PRIORITIES_PATH) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def opening_message(
    jd: str, *, company: str, title: str, s: st.Settings, cv: str, rules: Verdict
) -> str:
    notes = priorities_text() or "(none written)"
    rule_line = f"{rules.decision}: " + "; ".join(rules.blockers or rules.reasons or ["-"])
    return (
        f"<settings>\n{describe_settings(s)}\n</settings>\n\n"
        f"<their_notes>\n{notes}\n</their_notes>\n\n"
        f"<cv>\n{cv}\n</cv>\n\n"
        f"<rule_filter>\n{rule_line}\n</rule_filter>\n\n"
        f"<posting company={json.dumps(company)} title={json.dumps(title)}>\n{jd}\n</posting>"
    )


def parse_verdict(text: str, source: str) -> Verdict:
    """The first JSON object in the answer. Models wrap JSON in prose or code fences often
    enough that insisting on a bare object would fail on working answers."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("the model's answer contained no JSON verdict")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        raise LLMError("the model's verdict was not valid JSON") from None
    decision = str(data.get("decision", "")).strip().lower()
    if decision not in DECISIONS:
        raise LLMError(f"the model's decision was {decision!r}, not one of {DECISIONS}")

    def strings(key: str) -> list[str]:
        items = data.get(key) or []
        return [str(i).strip() for i in items if str(i).strip()] if isinstance(items, list) else []

    return Verdict(
        decision=decision,
        reasons=strings("reasons"),
        blockers=strings("blockers"),
        gaps=strings("gaps"),
        questions=strings("questions"),
        source=source,
    )


class Conversation:
    """The verdict, then any follow-up questions, in one thread with the model."""

    def __init__(self, client: Client, opening: str):
        self.client = client
        self.messages: list[dict] = [{"role": "user", "content": opening}]

    def verdict(self) -> Verdict:
        answer = self.client.chat(SYSTEM, self.messages)
        self.messages.append({"role": "assistant", "content": answer})
        return parse_verdict(answer, self.client.label)

    def ask(self, question: str) -> str:
        self.messages.append({"role": "user", "content": question})
        answer = self.client.chat(SYSTEM, self.messages)
        self.messages.append({"role": "assistant", "content": answer})
        return answer
