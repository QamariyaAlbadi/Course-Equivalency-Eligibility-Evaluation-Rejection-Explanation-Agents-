import pytest

from app.services import llm_client


class _Err(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


class _Client:
    def __init__(self, failing):
        self.calls = []
        self.models = self
        self._failing = failing

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        if model in self._failing:
            raise _Err(self._failing[model], f"{self._failing[model]} error")
        return f"ok:{model}"


def test_falls_back_to_next_model_on_503():
    c = _Client({"a": 503})
    assert llm_client.generate_with_fallback(c, "p", None, models=["a", "b"], pause=0) == "ok:b"
    assert c.calls == ["a", "b"]


def test_falls_back_on_404_and_429():
    c = _Client({"a": 404, "b": 429})
    assert llm_client.generate_with_fallback(c, "p", None, models=["a", "b", "c"], pause=0) == "ok:c"


def test_bad_key_is_not_hidden():
    c = _Client({"a": 403})
    with pytest.raises(_Err):
        llm_client.generate_with_fallback(c, "p", None, models=["a", "b"], pause=0)
    assert c.calls == ["a"]


def test_all_models_failing_raises_one_clear_error():
    c = _Client({"a": 503, "b": 503})
    with pytest.raises(RuntimeError, match="All configured Gemini models failed"):
        llm_client.generate_with_fallback(c, "p", None, models=["a", "b"], pause=0)
