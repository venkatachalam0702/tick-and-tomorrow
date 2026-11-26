import json

import pytest
import yaml
from PIL import Image

from pipeline import diagram, gemini, imagery, visuals
from pipeline.script import LintResult, Script, render_script_md

SPEC = {"title": "Tiny magnet", "elements": [
    {"type": "circle", "x": 8, "y": 4, "r": 1.2, "label": "O", "color": "red", "fill": True},
    {"type": "arrow", "from": [2, 2], "to": [5, 2], "label": "field"},
    {"type": "wave", "from": [1, 6], "to": [15, 6]},
    {"type": "box", "x": 11, "y": 1, "w": 3, "h": 2, "label": "Magnetron"},
    {"type": "label", "x": 3, "y": 5, "text": "positive", "target": [7, 4]},
    {"type": "line", "points": [[1, 1], [3, 3]], "dashed": True},
    {"type": "text", "x": 8, "y": 0.6, "text": "Hello", "size": "l"}]}


def _md():
    L = lambda t, vt, v: {"text": t, "claims": [], "visual_type": vt, "visual": v}
    data = {"sections": [
        {"kind": "hook", "title": "", "lines": [L("One.", "illustration", "Lasagna"), L("Two.", "same", "same")]},
        {"kind": "chapter", "title": "A", "lines": [L("Three.", "diagram", "Water molecule"),
                                                     L("Four.", "photo", "Magnetron photo"),
                                                     L("Five.", "same", "same")]},
        {"kind": "question", "title": "", "lines": [L("Six?", "text", "Question card")]}],
        "pronunciations": []}
    return render_script_md("T", Script.from_json(data), LintResult())


def test_cues_group_sentences_under_their_visual():
    cues = visuals.cues_from_script(_md())
    assert [(c.id, c.kind, c.sentences) for c in cues] == [
        ("V01", "illustration", [0, 1]), ("V02", "diagram", [2]), ("V03", "photo", [3, 4]), ("V04", "text", [5])]
    assert cues[2].narration == "Four. Five."


def test_diagram_renders_full_hd(tmp_path):
    p = diagram.render(SPEC, tmp_path / "d.png")
    assert Image.open(p).size == (1920, 1080)


@pytest.mark.parametrize("bad, msg", [
    ({"elements": []}, "no elements"),
    ({"elements": [{"type": "blob"}]}, "unknown element type"),
    ({"elements": [{"type": "box", "x": 1}]}, "needs 'y'"),
    ({"elements": [{"type": "arrow", "from": [1], "to": [2, 2]}]}, "must be \\[x, y\\]"),
])
def test_bad_diagram_specs_raise(tmp_path, bad, msg):
    with pytest.raises(diagram.DiagramError, match=msg):
        diagram.render(bad, tmp_path / "d.png")


COMPACT = {"title": "Oven parts", "elements": [
    {"type": "box", "at": [1, 2, 4, 2.5], "label": "Magnetron", "color": "warm", "fill": True},
    {"type": "wave", "at": [5.3, 3.25, 9.5, 3.25], "label": "", "color": "cool"},
    {"type": "circle", "at": [12, 3], "label": "Food", "color": "red"},          # radius missing: default
    {"type": "box", "at": [9.8, 6], "label": "Door", "color": "ink"},            # size missing: default
    {"type": "arrow", "at": [2, 1, 5, 1], "label": "power", "color": "ink"},
    {"type": "line", "at": [1, 0.6, 3, 0.6, 3, 1.5], "label": "", "color": "sub"},
    {"type": "text", "at": [8, 7], "label": "2.45 GHz", "color": "ink"},
    {"type": "label", "at": [7.4, 6.2, 7.4, 3.6], "label": "Microwaves", "color": "cool"}]}


def test_compact_layout_renders(tmp_path):
    assert Image.open(diagram.render(COMPACT, tmp_path / "c.png")).size == (1920, 1080)


def test_normalize_compact_to_long_form():
    assert diagram.normalize({"type": "Arrow", "at": [1, 2, 3, 4]}) | {} == {"type": "arrow", "from": [1, 2], "to": [3, 4]}
    el = diagram.normalize({"type": "label", "at": [1, 2, 3, 4], "label": "hi"})
    assert el["x"] == 1 and el["target"] == [3, 4] and el["text"] == "hi"
    assert diagram.normalize({"type": "line", "at": [0, 0, 1, 1, 2, 0]})["points"] == [[0, 0], [1, 1], [2, 0]]
    with pytest.raises(diagram.DiagramError, match="must be numbers"):
        diagram.normalize({"type": "box", "at": ["a"]})


def test_keywords_drop_filler_words():
    assert imagery.keywords("A close-up photo of the cavity magnetron inside a microwave oven", 3) == \
        "close-up cavity magnetron"


def test_flux_request_has_no_seed(tmp_path, monkeypatch):
    sent = {}

    class R:
        status_code = 200

        def json(self):
            import base64, io
            buf = io.BytesIO()
            Image.new("RGB", (64, 64)).save(buf, "PNG")
            return {"result": {"image": base64.b64encode(buf.getvalue()).decode()}}

    class S:
        def post(self, url, headers=None, json=None, timeout=None):
            sent.update(json)
            return R()

    monkeypatch.setenv("CF_ACCOUNT_ID", "a")
    monkeypatch.setenv("CF_API_TOKEN", "t")
    monkeypatch.setattr(imagery, "http", lambda: S())
    imagery.flux_illustration("a tube", tmp_path / "f.png", seed=1)
    assert set(sent) == {"prompt", "steps"} and "a tube" in sent["prompt"]


@pytest.mark.parametrize("lic, ok", [("CC BY-SA 4.0", True), ("CC0", True), ("Public domain", True),
                                     ("CC BY-NC 2.0", False), ("CC BY-ND 3.0", False), ("Fair use", False), ("", False)])
def test_license_allow_list(lic, ok):
    assert imagery.license_ok(lic) is ok


def test_text_card_and_fit(tmp_path):
    p = imagery.text_card("How do you beat cold spots?", "Tell us below", tmp_path / "t.png")
    assert Image.open(p).size == (1920, 1080)
    tall = Image.new("RGB", (600, 1200), "white")
    assert imagery.fit(tall).size == (1920, 1080) and imagery.fit(tall, "cover").size == (1920, 1080)


CFG = {"max_attempts": 3, "min_qa_score": 3, "photo_fallback": "illustration"}


def _fake_check(results):
    it = iter(results)
    return lambda c, path: dict(next(it))


def test_diagram_redrawn_after_failed_check(tmp_path, monkeypatch):
    c = visuals.Cue("V02", "diagram", "Water molecule", [2], "Three.", plan={"diagram": {"elements": []}})
    redraws = []
    monkeypatch.setattr(visuals, "_redraw", lambda c, problems: redraws.append(problems) or SPEC)
    monkeypatch.setattr(visuals, "vision_check", _fake_check([{"pass": True, "score": 5, "issues": []}]))
    visuals.make_one(c, tmp_path, CFG)
    # First attempt had no layout (error), second redrew it and passed.
    assert c.status == "ok" and c.attempts == 2 and redraws and "no diagram layout" in redraws[0][0]


def test_photo_falls_back_to_illustration(tmp_path, monkeypatch):
    c = visuals.Cue("V03", "photo", "Magnetron", [3], "Four.", plan={"search": "magnetron", "prompt": "a tube"})
    monkeypatch.setattr(imagery, "photo_candidates", lambda q: [])
    made = []
    monkeypatch.setattr(imagery, "flux_illustration",
                        lambda prompt, out, seed=0: made.append(prompt) or Image.new("RGB", (1920, 1080)).save(out))
    monkeypatch.setattr(visuals, "vision_check", _fake_check([{"pass": True, "score": 4, "issues": []}]))
    visuals.make_one(c, tmp_path, CFG)
    assert made == ["a tube"] and c.status == "ok" and c.source["note"] == "photo fallback"


def test_photo_tries_next_candidate_then_flags(tmp_path, monkeypatch):
    c = visuals.Cue("V03", "photo", "Magnetron", [3], "Four.", plan={"search": "magnetron"})
    cands = [{"url": f"u{i}", "title": f"t{i}", "author": "a", "license": "CC0", "page": "p", "provider": "x"} for i in range(3)]
    monkeypatch.setattr(imagery, "photo_candidates", lambda q: list(cands))
    got = []
    monkeypatch.setattr(imagery, "download_photo", lambda pick, out: got.append(pick["url"]) or Image.new("RGB", (1920, 1080)).save(out))
    monkeypatch.setattr(visuals, "vision_check", _fake_check([{"pass": False, "score": 2, "issues": ["wrong object"]}] * 3))
    visuals.make_one(c, tmp_path, CFG)
    assert got == ["u0", "u1", "u2"] and c.status == "flagged" and c.source["title"] == "t2"


def test_checker_down_keeps_image_but_flags(tmp_path, monkeypatch):
    c = visuals.Cue("V04", "text", "Question", [5], "Six?", plan={"headline": "Your trick?"})
    monkeypatch.setattr(visuals, "vision_check", _fake_check([{"pass": False, "score": 0, "issues": ["down"], "error": True}]))
    visuals.make_one(c, tmp_path, CFG)
    assert c.status == "flagged" and c.attempts == 1 and (tmp_path / "V04.png").exists()


def test_failed_illustration_leaves_placeholder(tmp_path, monkeypatch):
    c = visuals.Cue("V01", "illustration", "Lasagna", [0], "One.", plan={"prompt": "lasagna"})
    monkeypatch.setattr(imagery, "flux_illustration", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("CF down")))
    visuals.make_one(c, tmp_path, CFG)
    assert c.status == "missing" and (tmp_path / "V01.png").exists() and c.source["note"].startswith("placeholder")


def test_run_end_to_end(tmp_path, monkeypatch):
    ep = tmp_path / "ep"
    ep.mkdir()
    (ep / "script.md").write_text(_md())
    (ep / "topic.yaml").write_text(yaml.safe_dump({"title": "T"}))

    def fake_generate(prompt, *, role, json_schema=None, temperature=None, purpose="", images=None, max_tokens=None):
        if purpose.startswith("visual plan"):
            assert "V01 [illustration]" in prompt and "{cues}" not in prompt and max_tokens
            return {"assets": [{"id": "V01", "prompt": "lasagna"}, {"id": "V02", "diagram": SPEC},
                               {"id": "V03", "search": "magnetron"}, {"id": "V04", "headline": "Your trick?"}]}
        assert images and images[0][1] == "image/jpeg"
        return {"pass": True, "score": 5, "issues": []}

    monkeypatch.setattr(gemini, "generate", fake_generate)
    monkeypatch.setattr(imagery, "flux_illustration", lambda p, out, seed=0: Image.new("RGB", (1920, 1080)).save(out))
    monkeypatch.setattr(imagery, "photo_candidates", lambda q: [
        {"url": "u", "title": "Magnetron.jpg", "author": "NASA", "license": "Public domain", "page": "p", "provider": "Wikimedia Commons"}])
    monkeypatch.setattr(imagery, "download_photo", lambda pick, out: Image.new("RGB", (1920, 1080)).save(out))
    cues = visuals.run(ep, tmp_path / "out")
    assert all(c.status == "ok" for c in cues)
    assert (tmp_path / "out" / "contact_sheet.jpg").exists()
    credits = json.loads((ep / "credits.json").read_text())
    assert credits == [{"type": "photo", "title": "Magnetron.jpg", "author": "NASA", "license": "Public domain",
                        "page": "p", "provider": "Wikimedia Commons", "visual": "V03"}]
    assert len(json.loads((ep / "visuals.json").read_text())) == 4
