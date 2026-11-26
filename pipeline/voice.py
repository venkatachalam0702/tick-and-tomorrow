"""Stage 4: narrate the approved script with Kokoro, with word-level timings.

Usage:
  python -m pipeline.voice narrate episodes/<episode> [--voice af_heart] [--out out/<episode>]
  python -m pipeline.voice samples episodes/<episode> [--out out/samples]

narrate writes (media in --out, never committed; text in the episode folder):
  out/<episode>/narration.wav    normalised to -14 LUFS, for assembly
  out/<episode>/narration.mp3    for listening
  episodes/<episode>/timings.json  sentence and word start/end times (seconds)
  episodes/<episode>/captions.srt  captions built from the word times
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import config
from .config import ROOT
from .script import parse_script_md


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Sentence:
    section: str
    text: str
    claims: list[str]
    visual_type: str
    visual: str
    start: float = 0.0
    end: float = 0.0
    words: list[Word] = field(default_factory=list)


# ------------------------------------------------------------------ TTS engine

class KokoroTTS:
    """Thin wrapper so tests can swap in a fake engine."""

    def __init__(self, lang_code: str, voice: str, speed: float):
        from kokoro import KPipeline  # heavy import, only when narrating

        self.pipe = KPipeline(lang_code=lang_code)
        self.voice, self.speed = voice, speed

    def speak(self, text: str):
        """Return (audio float32 numpy array, [(word, start, end) | (word, None, None)])."""
        import numpy as np

        chunks, words, offset = [], [], 0.0
        sr = 24000
        for r in self.pipe(text, voice=self.voice, speed=self.speed, split_pattern=None):
            if r.audio is None:
                continue
            audio = r.audio.detach().cpu().numpy() if hasattr(r.audio, "detach") else np.asarray(r.audio)
            for t in r.tokens or []:
                s, e = getattr(t, "start_ts", None), getattr(t, "end_ts", None)
                if re.search(r"\w", t.text):
                    words.append((t.text, None if s is None else s + offset, None if e is None else e + offset))
            chunks.append(audio)
            offset += len(audio) / sr
        return (np.concatenate(chunks) if chunks else np.zeros(0, dtype="float32")), words


def apply_lexicon(text: str, lexicon: dict[str, str]) -> str:
    """Replace listed words with Kokoro's inline phoneme markup: [word](/IPA/)."""
    for word, ipa in lexicon.items():
        text = re.sub(rf"(?<![\w\[])({re.escape(word)})(?![\w\]])", rf"[\1](/{ipa}/)", text, flags=re.I)
    return text


def fill_missing_times(words: list[tuple], text: str, duration: float) -> list[Word]:
    """Use Kokoro's word times; where any are missing, spread words over the sentence by length."""
    if words and all(s is not None and e is not None for _, s, e in words):
        return [Word(w, float(s), float(e)) for w, s, e in words]
    tokens = [w for w, _, _ in words] or re.findall(r"[\w'’\-]+", text)
    total = sum(len(t) for t in tokens) or 1
    out, t = [], 0.0
    for tok in tokens:
        d = duration * len(tok) / total
        out.append(Word(tok, round(t, 3), round(t + d, 3)))
        t += d
    return out


# ------------------------------------------------------------------ narration

def sentences_from_script(md: str) -> list[Sentence]:
    script = parse_script_md(md)
    out = []
    for s in script.sections:
        label = f"{s.kind}: {s.title}" if s.title else s.kind
        for ln in s.lines:
            out.append(Sentence(label, ln.text, ln.claims, ln.visual_type, ln.visual))
    return out


def narrate(sentences: list[Sentence], tts, cfg: dict, lexicon: dict[str, str]):
    """Speak every sentence; return (audio array, sentences with absolute times)."""
    import numpy as np

    sr = cfg["sample_rate"]
    parts, t = [], 0.0
    prev_section = None
    for i, s in enumerate(sentences):
        if i:
            gap = cfg["pause_section"] if s.section != prev_section else cfg["pause_sentence"]
            parts.append(np.zeros(int(gap * sr), dtype="float32"))
            t += gap
        audio, words = tts.speak(apply_lexicon(s.text, lexicon))
        dur = len(audio) / sr
        s.start, s.end = round(t, 3), round(t + dur, 3)
        timed = fill_missing_times(words, s.text, dur)
        display = s.text.split()
        if len(display) == len(timed):  # keep the script's own spelling and punctuation for captions
            timed = [Word(d, w.start, w.end) for d, w in zip(display, timed)]
        s.words = [Word(w.text, round(t + w.start, 3), round(t + w.end, 3)) for w in timed]
        parts.append(audio.astype("float32"))
        t += dur
        prev_section = s.section
        print(f"[{i + 1}/{len(sentences)}] {dur:5.1f}s  {s.text[:60]}")
    return (np.concatenate(parts) if parts else np.zeros(0, dtype="float32")), sentences


def _ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(sentences: list[Sentence], max_chars: int = 42) -> str:
    """One caption line at a time, never crossing a sentence, at most max_chars characters."""
    cues = []
    for s in sentences:
        line: list[Word] = []
        for w in s.words:
            candidate = " ".join(x.text for x in line + [w])
            if line and len(candidate) > max_chars:
                cues.append(line)
                line = []
            line.append(w)
        if line:
            cues.append(line)
    out = []
    for n, cue in enumerate(cues, 1):
        out += [str(n), f"{_ts(cue[0].start)} --> {_ts(cue[-1].end)}", " ".join(w.text for w in cue), ""]
    return "\n".join(out)


def timings_json(sentences: list[Sentence], voice: str, total: float) -> dict:
    return {"voice": voice, "duration": round(total, 3), "sentences": [
        {"index": i, "section": s.section, "text": s.text, "claims": s.claims,
         "visual_type": s.visual_type, "visual": s.visual, "start": s.start, "end": s.end,
         "words": [[w.text, w.start, w.end] for w in s.words]} for i, s in enumerate(sentences)]}


def write_audio(audio, out_dir: Path, name: str, cfg: dict) -> None:
    import soundfile as sf

    out_dir.mkdir(parents=True, exist_ok=True)
    raw = out_dir / f"{name}_raw.wav"
    sf.write(raw, audio, cfg["sample_rate"])
    lufs = cfg["loudness_lufs"]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af",
                    f"loudnorm=I={lufs}:TP=-1.5:LRA=11", "-ar", str(cfg["sample_rate"]), str(out_dir / f"{name}.wav")],
                   check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out_dir / f"{name}.wav"),
                    "-codec:a", "libmp3lame", "-q:a", "2", str(out_dir / f"{name}.mp3")], check=True)
    raw.unlink()


def load_lexicon() -> dict[str, str]:
    f = ROOT / "voice" / "lexicon.yaml"
    return (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("words") or {} if f.exists() else {}


def run_narrate(ep: Path, out: Path, voice: str | None = None, tts=None, speed: float | None = None) -> dict:
    cfg = config.load()["voice"]
    voice = voice or cfg["voice"]
    tts = tts or KokoroTTS(cfg["lang_code"], voice, speed or cfg["speed"])
    sentences = sentences_from_script((ep / "script.md").read_text(encoding="utf-8"))
    audio, sentences = narrate(sentences, tts, cfg, load_lexicon())
    total = len(audio) / cfg["sample_rate"]
    write_audio(audio, out, "narration", cfg)
    data = timings_json(sentences, voice, total)
    (ep / "timings.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (ep / "captions.srt").write_text(build_srt(sentences, cfg["caption_max_chars"]), encoding="utf-8")
    print(f"Narration: {total / 60:.1f} min with voice {voice}")
    full = config.load()
    budget = float(full["script"].get("max_video_seconds", 300)) - float(full.get("assemble", {}).get("end_card_seconds", 6)) - 1
    if total > budget:
        fix = (speed or cfg["speed"]) * total / budget
        print(f"[warn] narration is {total:.0f} s; the video limit leaves {budget:.0f} s. "
              f"Shorten the script, or re-run with speed {min(fix + 0.01, 1.25):.2f}.")
    return data


def run_samples(ep: Path, out: Path, tts_factory=None) -> list[Path]:
    """Render the hook and promise with every candidate voice, so Venkat can pick one by ear."""
    cfg = config.load()["voice"]
    sentences = [s for s in sentences_from_script((ep / "script.md").read_text(encoding="utf-8"))
                 if s.section.startswith(("hook", "promise"))] or \
        sentences_from_script((ep / "script.md").read_text(encoding="utf-8"))[:4]
    made = []
    for v in cfg["candidates"]:
        tts = tts_factory(v) if tts_factory else KokoroTTS(cfg["lang_code"], v, cfg["speed"])
        fresh = [Sentence(s.section, s.text, s.claims, s.visual_type, s.visual) for s in sentences]
        audio, _ = narrate(fresh, tts, cfg, load_lexicon())
        name = "sample_" + v.replace(",", "+")
        write_audio(audio, out, name, cfg)
        made.append(out / f"{name}.mp3")
    return made


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["narrate", "samples"])
    ap.add_argument("episode", type=Path)
    ap.add_argument("--voice", default="")
    ap.add_argument("--speed", type=float, default=None, help="override voice.speed for this run, e.g. 1.1")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    ep = a.episode if a.episode.is_absolute() else ROOT / a.episode
    out = a.out or ROOT / "out" / ep.name
    if a.cmd == "narrate":
        run_narrate(ep, out, a.voice or None, speed=a.speed)
    else:
        for p in run_samples(ep, out):
            print(p)


if __name__ == "__main__":
    main()
