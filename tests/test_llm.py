import pytest

from finn import llm


class R:
    def __init__(self, status, js=None, text=""):
        self.status_code, self._js, self.text = status, js, text

    def json(self):
        if self._js is None:
            raise ValueError("no json")
        return self._js


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("FINN_LLM_API_KEY", "test-key")
    monkeypatch.setenv("FINN_FAST_MODEL", "gemini-2.5-flash-lite")
    monkeypatch.setattr(llm, "_resolved", {})
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    served = {"gemini-3.0-flash-lite", "gemini-3.0-flash", "gemini-3.0-pro"}
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append(json["model"])
        if json["model"] not in served:
            return R(404, [{"error": {"code": 404, "message": f"models/{json['model']} is not found for API version v1beta"}}])
        return R(200, {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"prompt_tokens": 10, "completion_tokens": 3}})

    def get(url, headers=None, timeout=None):
        return R(200, {"data": [{"id": f"models/{m}"} for m in sorted(served | {"text-embedding-004", "gemini-3.0-flash-preview-09-2026"})]})

    monkeypatch.setattr(llm.requests, "post", post)
    monkeypatch.setattr(llm.requests, "get", get)
    return calls


def test_retired_model_is_replaced_once_and_remembered(api):
    with llm.ledger() as led:
        assert llm.complete("fast", "sys", "first") == {"ok": True}
        assert llm.complete("fast", "sys", "second") == {"ok": True}
    assert api == ["gemini-2.5-flash-lite", "gemini-3.0-flash-lite", "gemini-3.0-flash-lite"]
    assert llm.model_for("fast") == "gemini-3.0-flash-lite" and led.calls == 2


def test_bad_key_fails_fast_with_a_hint(monkeypatch, api):
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: R(400, [{"error": {"message": "API key not valid. Please pass a valid API key."}}]))
    with pytest.raises(llm.LLMConfigError, match="FINN_LLM_API_KEY"):
        llm.complete("fast", "sys", "x")


def test_pick_model_prefers_newest_stable():
    ids = ["gemini-2.0-flash", "gemini-2.5-flash-lite", "gemini-3.0-flash-lite", "gemini-3.0-flash-lite-preview",
           "gemini-3.0-flash", "gemini-3.0-pro", "text-embedding-004", "gemma-3-27b-it"]
    assert llm.pick_model("fast", ids) == "gemini-3.0-flash-lite"
    assert llm.pick_model("smart", ids) == "gemini-3.0-flash"
    assert llm.pick_model("smart", ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]) == "llama-3.3-70b-versatile"
