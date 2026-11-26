from pipeline.facts import Source, parse_facts_md, quote_in_text, render_facts_md, verify
from pipeline.util import normalize

TEXT = ("A microwave oven heats food by exposing it to microwave radiation in the electromagnetic "
        "spectrum. Consumer ovens usually use 2.45 gigahertz (GHz)—a wavelength of 12.2 centimetres. "
        "Water, fat and other substances in the food absorb energy from the microwaves in a process "
        "called dielectric heating.[3]")


def test_quote_matches_despite_typography():
    t = normalize(TEXT)
    assert quote_in_text("Consumer ovens usually use 2.45 gigahertz (GHz)-a wavelength", t)
    assert quote_in_text("“absorb energy from the microwaves in a process called dielectric heating”", t)


def test_quote_with_ellipsis_must_keep_order():
    t = normalize(TEXT)
    assert quote_in_text("A microwave oven heats food ... called dielectric heating", t)
    assert not quote_in_text("called dielectric heating ... A microwave oven heats food", t)


def test_paraphrase_and_tiny_quotes_rejected():
    t = normalize(TEXT)
    assert not quote_in_text("Microwave ovens cook food using radio waves", t)
    assert not quote_in_text("food", t)


def _sources():
    return {
        "S1": Source("S1", "Microwave oven (Wikipedia)", "https://en.wikipedia.org/wiki/Microwave_oven", TEXT),
        "S2": Source("S2", "FDA", "https://www.fda.gov/x", "Most ovens operate at 2.45 gigahertz, a frequency set aside for this use."),
    }


def test_verify_statuses():
    raw = [
        {"section": "Basics", "claim": "Ovens use 2.45 GHz.", "evidence": [
            {"source_id": "S1", "quote": "Consumer ovens usually use 2.45 gigahertz (GHz)"},
            {"source_id": "S2", "quote": "Most ovens operate at 2.45 gigahertz"}]},
        {"section": "Basics", "claim": "The wavelength is 12.2 cm.", "evidence": [
            {"source_id": "S1", "quote": "a wavelength of 12.2 centimetres"}]},
        {"section": "Basics", "claim": "Water absorbs microwaves.", "evidence": [
            {"source_id": "S1", "quote": "absorb energy from the microwaves in a process called dielectric heating"}]},
        {"section": "Myths", "claim": "Microwaves make food radioactive.", "evidence": [
            {"source_id": "S1", "quote": "microwaves make food radioactive"}]},
        {"section": "Myths", "claim": "Bad source id.", "evidence": [
            {"source_id": "S9", "quote": "Consumer ovens usually use 2.45 gigahertz"}]},
    ]
    claims = verify(raw, _sources())
    assert [c.status for c in claims] == ["verified", "single-source", "verified", "dropped", "dropped"]
    assert claims[0].source_ids == ["S1", "S2"]


def test_render_and_parse_round_trip_after_human_edit():
    claims = verify([
        {"section": "Basics", "claim": "Ovens use 2.45 GHz.", "evidence": [
            {"source_id": "S1", "quote": "Consumer ovens usually use 2.45 gigahertz (GHz)"}]},
        {"section": "Basics", "claim": "Water absorbs microwaves.", "evidence": [
            {"source_id": "S1", "quote": "absorb energy from the microwaves in a process called dielectric heating"}]},
        {"section": "Myths", "claim": "Fake.", "evidence": [{"source_id": "S1", "quote": "not in the text at all here"}]},
    ], _sources())
    md = render_facts_md("How does a microwave work?", claims, _sources(), "none")
    assert "### C01 ⚠️ single-source" in md and "### C03" not in md.split("## Sources")[0]
    assert [c["id"] for c in parse_facts_md(md)] == ["C01", "C02"]
    # Venkat deletes C01 and rewords C02 on GitHub.
    edited = md.replace(md[md.index("### C01"):md.index("### C02")], "")
    edited = edited.replace("**Claim:** Water absorbs microwaves.", "**Claim:** Water and fat absorb microwave energy.")
    assert parse_facts_md(edited) == [{"id": "C02", "claim": "Water and fat absorb microwave energy."}]
