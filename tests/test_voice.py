import json
import re
import shutil

import numpy as np
import pytest
import yaml

from pipeline import voice
from pipeline.script import Script, render_script_md, LintResult

SR = 24000


class FakeTTS:
    """0.1 s of tone per word; optionally drops timestamps like a misbehaving engine."""

    def __init__(self, drop_times=False):
        self.drop_times, self.calls = drop_times, []

    def speak(self, text):
        self.calls.append(text)
        words = re.findall(r"\[(\w+)\]\(/[^/]+/\)|([\w'’\-]+)", text)
        words = [a or b for a, b in words]
        audio = (0.1 * np.sin(np.linspace(0, 440 * 2 * np.pi * 0.1 * len(words), int(SR * 0.1 * len(words))))).astype("float32")
        times = [(w, None, None) if self.drop_times else (w, i * 0.1, (i + 1) * 0.1) for i, w in enumerate(words)]
        return audio, times


def _script_md():
    data = {"sections": [
        {"kind": "hook", "title": "", "lines": [
            {"text": "Microwaves do not cook from the inside out.", "claims": ["C20"], "visual_type": "text", "visual": "MYTH"}]},
        {"kind": "chapter", "title": "The magnetron", "lines": [
            {"text": "A magnetron makes the waves.", "claims": ["C04"], "visual_type": "diagram", "visual": "Magnetron"},
            {"text": "It spins electrons past tiny cavities inside a strong magnet.", "claims": ["C04"], "visual_type": "same", "visual": "same"}]},
    ], "pronunciations": []}
    return render_script_md("T", Script.from_json(data), LintResult())


@pytest.fixture
def ep(tmp_path):
    e = tmp_path / "ep"
    e.mkdir()
    (e / "script.md").write_text(_script_md())
    return e


def test_narrate_times_and_pauses(ep):
    cfg = voice.config.load()["voice"]
    sents = voice.sentences_from_script((ep / "script.md").read_text())
    audio, sents = voice.narrate(sents, FakeTTS(), cfg, {})
    # 8 + 5 + 10 words at 0.1 s, plus one section pause and one sentence pause.
    expected = 2.3 + cfg["pause_section"] + cfg["pause_sentence"]
    assert abs(len(audio) / SR - expected) < 0.01
    assert sents[1].start == pytest.approx(0.8 + cfg["pause_section"], abs=0.002)
    assert sents[1].words[0].text == "A" and sents[1].words[0].start == sents[1].start
    assert sents[2].words[-1].end == pytest.approx(sents[2].end, abs=0.002)
    assert sents[2].visual_type == "same" and sents[1].section == "chapter: The magnetron"


def test_missing_timestamps_are_spread_by_length(ep):
    cfg = voice.config.load()["voice"]
    sents = voice.sentences_from_script((ep / "script.md").read_text())
    _, sents = voice.narrate(sents, FakeTTS(drop_times=True), cfg, {})
    w = sents[0].words
    assert w[0].start == sents[0].start and w[-1].end == pytest.approx(sents[0].end, abs=0.01)
    assert all(a.end <= b.start + 1e-6 for a, b in zip(w, w[1:]))


def test_lexicon_markup():
    out = voice.apply_lexicon("The Magnetron and magnetrons.", {"magnetron": "ˈmæɡnətɹɑn"})
    assert out == "The [Magnetron](/ˈmæɡnətɹɑn/) and magnetrons."


def test_srt_lines_are_short_and_ordered(ep):
    cfg = voice.config.load()["voice"]
    _, sents = voice.narrate(voice.sentences_from_script((ep / "script.md").read_text()), FakeTTS(), cfg, {})
    srt = voice.build_srt(sents, 20)
    blocks = [b.splitlines() for b in srt.strip().split("\n\n")]
    assert all(len(b[2]) <= 20 or " " not in b[2] for b in blocks)
    assert blocks[0][1] == "00:00:00,000 --> 00:00:00,800" or blocks[0][1].startswith("00:00:00,000 --> ")
    assert "magnet.\n" in srt and "\nout.\n" in srt


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_run_narrate_writes_files(ep, tmp_path):
    data = voice.run_narrate(ep, tmp_path / "out", voice="af_test", tts=FakeTTS())
    assert (tmp_path / "out" / "narration.wav").exists() and (tmp_path / "out" / "narration.mp3").exists()
    saved = json.loads((ep / "timings.json").read_text())
    assert saved["voice"] == "af_test" and len(saved["sentences"]) == 3 == len(data["sentences"])
    assert (ep / "captions.srt").read_text().startswith("1\n00:00:00,000 --> ")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_samples_one_file_per_candidate(ep, tmp_path, monkeypatch):
    cfg = voice.config.load()["voice"]
    monkeypatch.setitem(cfg, "candidates", ["af_heart", "af_heart,af_bella"])
    made = voice.run_samples(ep, tmp_path / "s", tts_factory=lambda v: FakeTTS())
    assert [p.name for p in made] == ["sample_af_heart.mp3", "sample_af_heart+af_bella.mp3"]
    assert all(p.exists() for p in made)
