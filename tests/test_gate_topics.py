import pytest

from pipeline.gate import GateError, resolve_topic
from pipeline.topics import rank, render_issue

IDEAS = [
    {"title": "How does GPS know where you are?", "wiki": "Global Positioning System"},
    {"title": "How does a microwave work?", "wiki": "Microwave oven", "pinned": True},
    {"title": "How does Wi-Fi send data through the air?", "wiki": "Wi-Fi"},
    {"title": "How does an LED make light?", "wiki": "Light-emitting diode"},
]
VIEWS = {"Global Positioning System": 50_000, "Microwave oven": 10_000, "Wi-Fi": 90_000, "Light-emitting diode": 1}


def _issue():
    top = rank(IDEAS, {"how does an led make light?"}, 30, views=lambda w, d: VIEWS[w])
    return top, render_issue(top, 30)


def test_rank_pins_then_views_and_skips_used():
    top, _ = _issue()
    assert [i["wiki"] for i in top] == ["Microwave oven", "Wi-Fi", "Global Positioning System"]


def test_approve_by_number():
    _, body = _issue()
    assert resolve_topic("/approve 1", body) == ("How does a microwave work?", "Microwave oven")
    assert resolve_topic("/approve #3\nthanks", body) == ("How does GPS know where you are?", "Global Positioning System")


def test_approve_by_ticked_box():
    _, body = _issue()
    body = body.replace("- [ ] **2.", "- [x] **2.")
    assert resolve_topic("/approve", body) == ("How does Wi-Fi send data through the air?", "Wi-Fi")


def test_custom_topic():
    assert resolve_topic("/topic How do lasers work? | Laser", "") == ("How do lasers work?", "Laser")
    assert resolve_topic("/topic Why is the sky blue?", "") == ("Why is the sky blue?", "")


@pytest.mark.parametrize("comment", ["/approve", "/approve 9", "/approve two", "looks good", "/topic"])
def test_bad_comments_explain_themselves(comment):
    _, body = _issue()
    with pytest.raises(GateError):
        resolve_topic(comment, body)
