import copy
import json

import pytest
import yaml

from pipeline import config, gemini, script as sc

IDS = {"C01", "C02", "C03", "C04"}
SOURCE = ("A cavity magnetron generates microwaves using the interaction of a stream of electrons "
          "with a magnetic field, while moving past a series of cavity resonators.")


def _line(text, claims=(), vt="same", visual="same"):
    return {"text": text, "claims": list(claims), "visual_type": vt, "visual": visual}


def good_json(sentence_words=12, per_chapter=18):
    filler = " ".join(["word"] * (sentence_words - 2))
    chap = lambda n: {"kind": "chapter", "title": f"Part {n}", "lines": [
        _line(f"Chapter {['one', 'two', 'three'][n - 1]} opens {filler}.", ["C01"], "diagram", "A labelled cutaway."),
        *[_line(f"More detail here {filler}.", ["C02"], "same" if i % 3 else "photo",
                "same" if i % 3 else "A photo of a kitchen.") for i in range(per_chapter - 1)]]}
    return {"sections": [
        {"kind": "hook", "lines": [_line("Your microwave is not tuned to water at all.", ["C03"], "text", "MYTH")]},
        {"kind": "promise", "lines": [_line("By the end you will know what really heats your food.", [], "illustration", "Steam over a bowl.")]},
        chap(1), chap(2), chap(3),
        {"kind": "recap", "lines": [_line("Electrons, waves and wiggling molecules: that is the whole trick.", [], "text", "Recap")]},
        {"kind": "question", "lines": [_line("What is the strangest thing you have microwaved?", [], "text", "Comment below")]},
    ], "pronunciations": [{"term": "magnetron", "say": "MAG-neh-tron"}]}


def test_good_script_passes():
    s = sc.Script.from_json(good_json())
    assert 600 <= s.word_count <= 750, s.word_count
    r = sc.lint(s, IDS, [SOURCE])
    assert r.ok, r.errors


@pytest.mark.parametrize("mutate, expect", [
    (lambda d: d["sections"].pop(0), "Sections must be"),
    (lambda d: d["sections"][2]["lines"].__setitem__(1, _line("It runs at 2.45 gigahertz in most homes today, really.")), "number has no claim"),
    (lambda d: d["sections"][2]["lines"][0]["claims"].append("C99"), "Unknown claim id C99"),
    (lambda d: d["sections"][2]["lines"][0].__setitem__("visual_type", "video"), "Unknown visual type"),
    (lambda d: d["sections"][0]["lines"][0].__setitem__("text", "Without further ado, microwaves are not tuned to water."), "Filler phrase"),
    (lambda d: d["sections"][0]["lines"].append(_line(" ".join(["long"] * 40), ["C01"], "same", "same")), "Hook is"),
    (lambda d: d["sections"][2]["lines"].__setitem__(1, _line(
        "A cavity magnetron generates microwaves using the interaction of a stream of electrons with a magnet.", ["C04"])), "Copies 10+ words"),
    (lambda d: [d["sections"][i].__setitem__("lines", d["sections"][i]["lines"][:5]) for i in (2, 3, 4)], "words; need"),
    (lambda d: [d["sections"][i]["lines"].extend(d["sections"][i]["lines"][1:10]) for i in (2, 3, 4)], "words; need"),
    (lambda d: d["sections"].insert(5, copy.deepcopy(d["sections"][2])) or d["sections"].insert(5, copy.deepcopy(d["sections"][2])), "chapters; need"),
])
def test_lint_catches(mutate, expect):
    d = copy.deepcopy(good_json())
    mutate(d)
    r = sc.lint(sc.Script.from_json(d), IDS, [SOURCE])
    assert any(expect in e for e in r.errors), r.errors


def test_markdown_round_trip_and_human_edit():
    s = sc.Script.from_json(good_json())
    md = sc.render_script_md("How does a microwave work?", s, sc.lint(s, IDS, [SOURCE]))
    back = sc.parse_script_md(md)
    assert back.to_json() == s.to_json()
    # Venkat rewrites the hook on GitHub and drops a tag.
    edited = md.replace("Your microwave is not tuned to water at all. [C03]",
                        "Here is a secret: your microwave does not target water. [C03, C01]")
    hook = sc.parse_script_md(edited).sections[0].lines[0]
    assert hook.text == "Here is a secret: your microwave does not target water."
    assert hook.claims == ["C03", "C01"] and hook.visual_type == "text"


def _episode(tmp_path):
    ep = tmp_path / "episodes" / "2026-11-26-how-does-a-microwave-work"
    (ep / "sources").mkdir(parents=True)
    (ep / "sources" / "S1.md").write_text("---\nid: S1\n---\n\n" + SOURCE)
    (ep / "topic.yaml").write_text(yaml.safe_dump({"title": "How does a microwave work?", "angle": "x"}))
    (ep / "facts.md").write_text("## Claims\n\n" + "".join(
        f"### {c}\n**Claim:** Fact {c}.\n> \"q\" ([S1](u))\n\n" for c in sorted(IDS)) + "C05 deleted\n## Sources\n")
    (ep / "facts.json").write_text(json.dumps([]))
    return ep


def test_write_repairs_once_and_saves(tmp_path, monkeypatch):
    ep = _episode(tmp_path)
    # facts.md has only 4 claims: require fewer for this test.
    calls = []
    bad = good_json()
    bad["sections"][2]["lines"][0]["claims"] = ["C77"]

    def fake(prompt, *, role, json_schema=None, temperature=None, purpose=""):
        calls.append(purpose)
        assert role == "writer"
        if purpose == "script":
            assert "C04: Fact C04." in prompt
            return bad
        assert "Unknown claim id C77" in prompt
        return good_json()

    monkeypatch.setattr(gemini, "generate", fake)
    monkeypatch.setattr(sc, "parse_facts_md", lambda md: [{"id": c, "claim": f"Fact {c}."} for c in sorted(IDS)] * 2)
    s, r = sc.write(ep)
    assert calls == ["script", "script repair 1"] and r.ok
    sc.save(ep, s, r)
    md = (ep / "script.md").read_text()
    assert "✅ all checks pass" in md
    assert yaml.safe_load((ep / "topic.yaml").read_text())["status"] == "gate-3-script"
    monkeypatch.undo()
    assert sc.lint_file(ep / "script.md").ok


def test_write_refuses_with_too_few_facts(tmp_path):
    ep = _episode(tmp_path)
    with pytest.raises(SystemExit, match="Only 4 approved facts"):
        sc.write(ep)


def test_parse_survives_deleted_start_marker():
    s = sc.Script.from_json(good_json())
    md = sc.render_script_md("T", s, sc.lint(s, IDS, [SOURCE]))
    edited = md.replace(sc.SCRIPT_START, "").replace(sc.SCRIPT_END, "")
    assert sc.parse_script_md(edited).to_json() == s.to_json()
    with pytest.raises(ValueError, match="Hook"):
        sc.parse_script_md("# Script\n\nno sections here")
