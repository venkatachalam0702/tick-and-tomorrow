import json
import shutil
import subprocess

import pytest
from PIL import Image

from pipeline import assemble

TIMINGS = {"duration": 10.0, "sentences": [
    {"index": 0, "start": 0.0, "end": 3.0}, {"index": 1, "start": 3.2, "end": 5.0},
    {"index": 2, "start": 5.3, "end": 8.0}, {"index": 3, "start": 8.2, "end": 10.0}]}
CUES = [{"id": "V01", "kind": "photo", "sentences": [0, 1], "file": "V01.png"},
        {"id": "V02", "kind": "diagram", "sentences": [2], "file": "V02.png"},
        {"id": "V03", "kind": "text", "sentences": [3], "file": "V03.png"}]


def test_timeline_follows_sentence_starts():
    segs = assemble.build_timeline(TIMINGS, CUES, tail=0.5)
    assert [(s.id, s.start, s.end) for s in segs] == [("V01", 0.0, 5.3), ("V02", 5.3, 8.2), ("V03", 8.2, 10.5)]


def test_timeline_survives_cue_without_sentences():
    cues = [dict(CUES[0]), {"id": "V02", "kind": "text", "sentences": []}, dict(CUES[2])]
    segs = assemble.build_timeline(TIMINGS, cues)
    assert all(b.start > a.start for a, b in zip(segs, segs[1:])) and segs[1].file == "V02.png"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_short_video_end_to_end(tmp_path, monkeypatch):
    ep = tmp_path / "ep"
    ep.mkdir()
    (ep / "timings.json").write_text(json.dumps(TIMINGS))
    (ep / "visuals.json").write_text(json.dumps(CUES))
    vis = tmp_path / "vis"
    vis.mkdir()
    for c, col in zip(CUES, ["red", "green", "blue"]):
        Image.new("RGB", (800, 600), col).save(vis / c["file"])  # 4:3 photo gets cropped/padded to 16:9
    audio = tmp_path / "n.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=10",
                    "-ar", "24000", str(audio)], check=True)
    cfg = assemble.config.load()
    monkeypatch.setitem(cfg["assemble"], "end_card_seconds", 2)
    monkeypatch.setitem(cfg["assemble"], "preset", "ultrafast")
    out = assemble.assemble(ep, audio, vis, tmp_path / "out")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height,codec_type",
                            "-of", "json", str(out)], capture_output=True, text=True, check=True)
    info = json.loads(probe.stdout)
    assert abs(float(info["format"]["duration"]) - 12.6) < 0.3
    v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    assert (v["width"], v["height"]) == (1920, 1080)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert [s["id"] for s in json.loads((tmp_path / "out" / "timeline.json").read_text())][-1] == "END"
