"""End-to-end research run with Wikipedia and Gemini replaced by fakes (no network)."""
import yaml

from pipeline import gemini, research, wikipedia
from pipeline.facts import parse_facts_md

ARTICLE = ("A microwave oven is an electric oven that heats and cooks food by exposing it to "
           "electromagnetic radiation in the microwave frequency range. Consumer ovens usually use "
           "2.45 gigahertz (GHz). The microwaves are produced by a vacuum tube called a cavity magnetron. "
           * 5)


def test_research_run_writes_episode(tmp_path, monkeypatch):
    monkeypatch.setattr(wikipedia, "resolve_titles", lambda ts: [t for t in ts if t == "Microwave oven"])
    monkeypatch.setattr(wikipedia, "article", lambda t: {
        "title": t, "url": "https://en.wikipedia.org/wiki/Microwave_oven", "text": ARTICLE,
        "extlinks": ["https://www.youtube.com/watch?v=1"]})

    def fake_generate(prompt, *, role, json_schema=None, temperature=None, purpose=""):
        gemini.CALL_LOG.append({"model": role, "role": role, "purpose": purpose, "attempt": 1})
        if purpose == "pick sources":
            return {"wikipedia_titles": ["Microwave oven", "Not A Real Page"],
                    "key_questions": ["What makes the heat?"], "angle": "The tube inside."}
        assert "=== S1: Microwave oven (Wikipedia) ===" in prompt
        return {"claims": [
            {"section": "Basics", "claim": "A magnetron makes the microwaves.", "evidence": [
                {"source_id": "S1", "quote": "The microwaves are produced by a vacuum tube called a cavity magnetron"}]},
            {"section": "Basics", "claim": "Made-up claim.", "evidence": [
                {"source_id": "S1", "quote": "microwaves are produced by tiny elves inside the door"}]},
        ]}

    monkeypatch.setattr(gemini, "generate", fake_generate)
    gemini.CALL_LOG.clear()
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    ep = research.run("How does a microwave work?", "Microwave oven", episodes_dir=tmp_path)

    topic = yaml.safe_load((ep / "topic.yaml").read_text())
    assert topic["wiki"] == "Microwave oven" and topic["angle"] == "The tube inside."
    assert (ep / "sources" / "S1.md").read_text().startswith("---\nid: S1")
    claims = parse_facts_md((ep / "facts.md").read_text())
    assert [c["claim"] for c in claims] == ["A magnetron makes the microwaves."]
    assert len(gemini.CALL_LOG) == 2
    assert out.read_text() == f"dir={ep}\n"


def test_domain_filter():
    cfg = research.config.load()["research"]
    assert research.domain_ok("https://www.fda.gov/radiation/microwave-ovens", cfg)
    assert research.domain_ok("https://physics.mit.edu/page", cfg)
    assert not research.domain_ok("https://www.youtube.com/watch?v=x", cfg)
    assert not research.domain_ok("https://randomblog.com/microwaves", cfg)
    assert not research.domain_ok("https://www.nasa.gov/image.jpg", cfg)
