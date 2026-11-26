import pytest

from pipeline import config, gemini


class Resp:
    def __init__(self, code, payload=None, text=""):
        self.status_code, self._p, self.text = code, payload, text

    def json(self):
        return self._p


OK = {"candidates": [{"content": {"parts": [{"text": '{"a": 1}'}]}}]}


@pytest.fixture
def no_wait(monkeypatch):
    cfg = config.load()
    monkeypatch.setitem(cfg["gemini"], "retry_wait_seconds", 0)
    monkeypatch.setitem(cfg["gemini"], "writer_model", "flash")
    monkeypatch.setitem(cfg["gemini"], "writer_fallbacks", ["lite"])
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    gemini.CALL_LOG.clear()


def test_overloaded_model_falls_back(monkeypatch, no_wait):
    seen = []

    def post(url, **kw):
        seen.append(url)
        return Resp(503, text="high demand") if "/flash:" in url else Resp(200, OK)

    monkeypatch.setattr(gemini.requests, "post", post)
    assert gemini.generate("hi", role="writer", json_schema={}) == {"a": 1}
    assert sum("/flash:" in u for u in seen) == config.load()["gemini"]["max_retries"]
    assert "/lite:" in seen[-1]


def test_retry_then_success_on_same_model(monkeypatch, no_wait):
    answers = iter([Resp(503, text="busy"), Resp(200, OK)])
    monkeypatch.setattr(gemini.requests, "post", lambda url, **kw: next(answers))
    assert gemini.generate("hi", role="writer", json_schema={}) == {"a": 1}
    assert [c["model"] for c in gemini.CALL_LOG] == ["flash", "flash"]


def test_bad_request_stops_immediately(monkeypatch, no_wait):
    monkeypatch.setattr(gemini.requests, "post", lambda url, **kw: Resp(400, text="bad schema"))
    with pytest.raises(gemini.GeminiError, match="rejected"):
        gemini.generate("hi", role="writer", json_schema={})
    assert len(gemini.CALL_LOG) == 1


def test_everything_down_raises(monkeypatch, no_wait):
    monkeypatch.setattr(gemini.requests, "post", lambda url, **kw: Resp(503, text="busy"))
    with pytest.raises(gemini.GeminiError, match="flash: .*lite: "):
        gemini.generate("hi", role="writer")
