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


def test_listed_but_refused_model_follows_the_api_suggestion(monkeypatch, api):
    msg = ("This model models/gemini-2.5-flash-lite is no longer available to new users. Please update your code "
           "to use models/gemini-3.0-flash-lite for the latest features and improvements.")
    served = {"gemini-3.0-flash-lite"}

    def post(url, json=None, headers=None, timeout=None):
        if json["model"] not in served:
            return R(404, [{"error": {"code": 404, "message": msg}}])
        return R(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})

    def get(url, headers=None, timeout=None):  # the refused model is still listed, as Gemini does
        return R(200, {"data": [{"id": "models/gemini-2.5-flash-lite"}, {"id": "models/gemini-3.0-flash-lite"},
                                {"id": "models/gemini-3.0-flash"}]})
    monkeypatch.setattr(llm.requests, "post", post)
    monkeypatch.setattr(llm.requests, "get", get)
    assert llm.complete("fast", "sys", "x") == {"ok": True}
    assert llm.model_for("fast") == "gemini-3.0-flash-lite"


def test_pick_model_on_the_list_your_key_returned():
    ids = ("gemini-2.5-flash, gemini-2.5-pro, gemini-2.5-flash-preview-tts, gemini-2.5-pro-preview-tts, gemma-4-26b-a4b-it, "
           "gemini-flash-latest, gemini-flash-lite-latest, gemini-pro-latest, gemini-2.5-flash-lite, gemini-2.5-flash-image, "
           "gemini-3-flash-preview, gemini-3.1-pro-preview, gemini-3.1-flash-lite-preview, gemini-3.1-flash-lite, "
           "gemini-3.1-flash-image, gemini-3.1-flash-lite-image, gemini-3.5-flash, gemini-3.5-flash-lite").split(", ")
    assert llm.pick_model("fast", ids) == "gemini-3.5-flash-lite"
    assert llm.pick_model("smart", ids) == "gemini-3.5-flash"


def test_thinking_model_that_runs_out_of_tokens_gets_more(monkeypatch, api):
    budgets = []

    def post(url, json=None, headers=None, timeout=None):
        budgets.append(json["max_tokens"])
        assert json.get("reasoning_effort") == "low"
        if len(budgets) == 1:  # Gemini's shape when all tokens went on thinking: no content key at all
            return R(200, {"choices": [{"message": {"role": "assistant"}, "finish_reason": "length"}]})
        return R(200, {"choices": [{"message": {"content": [{"type": "text", "text": '{"ok": true}'}]}}]})
    monkeypatch.setenv("FINN_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
    monkeypatch.setenv("FINN_FAST_MODEL", "gemini-3.0-flash-lite")
    monkeypatch.setattr(llm.requests, "post", post)
    assert llm.complete("fast", "sys", "think hard", max_tokens=20) == {"ok": True}
    assert budgets[1] == budgets[0] * 2


def test_provider_without_reasoning_effort_is_retried_without_it(monkeypatch, api):
    sent = []

    def post(url, json=None, headers=None, timeout=None):
        sent.append("reasoning_effort" in json)
        if "reasoning_effort" in json:
            return R(400, {"error": {"message": "Unknown parameter: reasoning_effort"}})
        return R(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})
    monkeypatch.setenv("FINN_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
    monkeypatch.setenv("FINN_FAST_MODEL", "gemini-3.0-flash-lite")
    monkeypatch.setattr(llm.requests, "post", post)
    assert llm.complete("fast", "sys", "y") == {"ok": True} and sent == [True, False]
