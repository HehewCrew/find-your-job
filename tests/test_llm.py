"""The optional LLM verdict, tested offline: request shapes, answer parsing, and the gate
jobs.paste puts in front of the CV build. Nothing here opens a network connection."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobs import llm, paste, settings  # noqa: E402

BASE = {
    "you": {"home_country": "Portugal", "regions": ["europe"]},
    "roles": [{"name": "qa", "titles": ["qa", "tester"], "cv": "automation_qa"}],
}

JD = (
    "Norwegian Tech Linguistic Tester\n\nRemote - Anywhere\n\n"
    "You will proofread content localized to Norwegian and QA mobile apps. "
    "Requirements: native-level Norwegian, 3+ years of translation experience, "
    "strong English, familiarity with Android and iOS."
)


def cfg(llm_cfg: dict | None = None) -> settings.Settings:
    data = copy.deepcopy(BASE)
    if llm_cfg is not None:
        data["llm"] = llm_cfg
    return settings.from_dict(data)


# --- configuration ----------------------------------------------------------------


def test_no_provider_means_no_client():
    assert llm.Client.from_settings(cfg(), env={}) is None


def test_a_configured_provider_without_its_key_is_an_error_not_a_silent_skip():
    with pytest.raises(llm.LLMError, match="ANTHROPIC_API_KEY"):
        llm.Client.from_settings(cfg({"provider": "anthropic"}), env={})


def test_anthropic_defaults_its_model():
    c = llm.Client.from_settings(cfg({"provider": "anthropic"}), env={"ANTHROPIC_API_KEY": "k"})
    assert c.model == llm.ANTHROPIC_DEFAULT_MODEL


def test_openai_needs_a_model_named():
    with pytest.raises(llm.LLMError, match="llm.model"):
        llm.Client.from_settings(cfg({"provider": "openai"}), env={"OPENAI_API_KEY": "k"})


def test_a_custom_key_variable_is_honoured():
    s = cfg(
        {
            "provider": "openai-compatible",
            "base_url": "https://openrouter.ai/api/v1",
            "model": "some/model",
            "api_key_env": "OPENROUTER_API_KEY",
        }
    )
    c = llm.Client.from_settings(s, env={"OPENROUTER_API_KEY": "k"})
    assert c.api_key == "k"


def test_a_local_server_needs_no_key():
    s = cfg(
        {"provider": "openai-compatible", "base_url": "http://localhost:11434/v1", "model": "m"}
    )
    c = llm.Client.from_settings(s, env={})
    url, headers, _ = c.request("sys", [{"role": "user", "content": "hi"}])
    assert url == "http://localhost:11434/v1/chat/completions"
    assert "authorization" not in headers


# --- request shapes ---------------------------------------------------------------


def test_anthropic_request_shape():
    c = llm.Client("anthropic", "claude-opus-5", "secret")
    url, headers, body = c.request("be brief", [{"role": "user", "content": "hi"}])
    data = json.loads(body)
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "secret"
    assert headers["anthropic-version"] == llm.ANTHROPIC_VERSION
    assert data["system"] == "be brief" and data["messages"][0]["content"] == "hi"
    assert data["fallbacks"] == "default"
    assert headers["anthropic-beta"] == llm.ANTHROPIC_FALLBACK_BETA


def test_the_fallback_field_is_only_sent_to_models_that_take_it():
    c = llm.Client("anthropic", "claude-haiku-4-5", "secret")
    _, headers, body = c.request("s", [{"role": "user", "content": "hi"}])
    assert "fallbacks" not in json.loads(body) and "anthropic-beta" not in headers


def test_openai_request_puts_the_system_prompt_first():
    c = llm.Client("openai", "some-model", "secret")
    url, headers, body = c.request("be brief", [{"role": "user", "content": "hi"}])
    data = json.loads(body)
    assert url.endswith("/chat/completions")
    assert headers["authorization"] == "Bearer secret"
    assert data["messages"][0] == {"role": "system", "content": "be brief"}


def test_answers_are_read_per_provider():
    a = llm.Client("anthropic", "m", "k").text_of(
        {"content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "hello"}]}
    )
    o = llm.Client("openai", "m", "k").text_of({"choices": [{"message": {"content": "hello"}}]})
    assert a == o == "hello"


def test_a_refusal_is_an_error():
    with pytest.raises(llm.LLMError, match="declined"):
        llm.Client("anthropic", "m", "k").text_of({"stop_reason": "refusal", "content": []})


# --- parsing the verdict ----------------------------------------------------------


def test_a_verdict_wrapped_in_prose_and_fences_still_parses():
    text = 'Here you go:\n```json\n{"decision": "Skip", "blockers": ["native Norwegian"]}\n```'
    v = llm.parse_verdict(text, "x")
    assert v.decision == "skip" and v.blockers == ["native Norwegian"]


@pytest.mark.parametrize(
    "text", ["no json here", '{"decision": "maybe"}', '{"decision": "apply", broken}']
)
def test_an_unusable_verdict_is_an_error(text):
    with pytest.raises(llm.LLMError):
        llm.parse_verdict(text, "x")


# --- paste's gate -----------------------------------------------------------------


class FakeClient(llm.Client):
    """Answers from a script instead of the network."""

    def __init__(self, *answers: str):
        super().__init__("anthropic", "fake", "k")
        self.answers = list(answers)
        self.seen: list[list[dict]] = []

    def chat(self, system, messages):
        self.seen.append(copy.deepcopy(messages))
        return self.answers.pop(0)


def _with_client(monkeypatch, client):
    monkeypatch.setattr(llm.Client, "from_settings", classmethod(lambda cls, s, env=None: client))


def test_the_model_can_catch_what_the_rules_pass(monkeypatch):
    """The Welo Global tester scored 121 on the rules; the blocker was in the text."""
    skip = json.dumps({"decision": "skip", "blockers": ["requires native-level Norwegian"]})
    _with_client(monkeypatch, FakeClient(skip))
    a = paste.assess(JD, company="Welo", title="Norwegian Tech Linguistic Tester", cfg=cfg())
    assert a.rules.decision != "skip"
    assert a.verdict.decision == "skip" and a.disagree
    assert "Norwegian" in a.verdict.blockers[0]


def test_follow_up_questions_carry_the_whole_thread(monkeypatch):
    fake = FakeClient(json.dumps({"decision": "consider"}), "It says three years.")
    _with_client(monkeypatch, fake)
    a = paste.assess(JD, company="Welo", title="Tester", cfg=cfg())
    assert a.conversation.ask("How much experience?") == "It says three years."
    # The follow-up saw the JD, the first verdict, and the question - in that order.
    roles = [m["role"] for m in fake.seen[-1]]
    assert roles == ["user", "assistant", "user"]
    assert "Norwegian" in fake.seen[-1][0]["content"]


def test_a_failing_model_falls_back_to_the_rules(monkeypatch):
    class Broken(FakeClient):
        def chat(self, system, messages):
            raise llm.LLMError("could not reach anthropic")

    _with_client(monkeypatch, Broken())
    a = paste.assess(JD, company="Welo", title="Tester", cfg=cfg())
    assert a.model is None and "could not reach" in a.llm_error
    assert a.verdict is a.rules


def _run(monkeypatch, tmp_path, verdict: str, *extra: str) -> tuple[int, list]:
    """paste.main with the model and the CV build stubbed out."""
    _with_client(monkeypatch, FakeClient(json.dumps({"decision": verdict})))
    monkeypatch.setattr(settings, "current", lambda: cfg())
    built: list = []
    monkeypatch.setattr(
        paste.tl,
        "fit",
        lambda t, report=None: (
            built.append(t)
            or paste.tl.CvResult(t["company"], t["variant"], keywords_kept=len(t["matched"]))
        ),
    )
    monkeypatch.setattr(paste.tl, "write_jd", lambda *a, **k: None)
    monkeypatch.setattr(paste.sys.stdin, "isatty", lambda: False, raising=False)
    jd = tmp_path / "jd.txt"
    jd.write_text(JD, encoding="utf-8")
    briefs = tmp_path / "BRIEFS.md"
    code = paste.main(["--file", str(jd), "--company", "Welo", "--briefs", str(briefs), *extra])
    return code, built


def test_a_skip_builds_nothing_and_exits_1(monkeypatch, tmp_path):
    code, built = _run(monkeypatch, tmp_path, "skip")
    assert code == 1 and built == []
    assert not (tmp_path / "BRIEFS.md").exists()


def test_force_builds_through_a_skip(monkeypatch, tmp_path):
    code, built = _run(monkeypatch, tmp_path, "skip", "--force")
    assert code == 0 and len(built) == 1
    assert "Welo" in (tmp_path / "BRIEFS.md").read_text(encoding="utf-8")


def test_an_apply_builds_without_force(monkeypatch, tmp_path):
    code, built = _run(monkeypatch, tmp_path, "apply")
    assert code == 0 and len(built) == 1


def test_dry_run_gives_the_verdict_and_builds_nothing(monkeypatch, tmp_path):
    code, built = _run(monkeypatch, tmp_path, "skip", "--dry-run")
    assert code == 0 and built == []


def test_an_unknown_variant_is_refused_up_front(monkeypatch, tmp_path):
    code, _ = _run(monkeypatch, tmp_path, "apply", "--variant", "astronaut")
    assert code == 2
