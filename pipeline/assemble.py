"""Stage 6: put the voice-over and the visuals together into the final video (FFmpeg, CPU).

Usage:
  python -m pipeline.assemble episodes/<episode> --audio out/narration.wav --visuals out/visuals [--out out]

Inputs:  timings.json (sentence start/end times from the voice stage), visuals.json (which image goes
         with which sentences), the narration audio and the images.
Output:  out/video.mp4 (1920x1080, 30 fps, H.264 + AAC, -14 LUFS voice) and out/timeline.json.

What it does:
  - each visual is on screen from the first word of its sentences until the next visual starts
  - photos and illustrations drift with a slow zoom (Ken Burns); diagrams and text cards stay still
    so their labels stay sharp
  - short cross-fades between visuals
  - the channel watermark in the bottom-right corner
  - an end card (logo + subscribe) long enough for YouTube end screens
  - optional background music (a YouTube Audio Library file in assets/music/), ducked under the voice
  - optional burned-in captions (off by default: captions.srt is uploaded to YouTube instead)
No Gemini calls.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import textwrap
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config, imagery
from .brand import COLORS, H, W, font_path
from .config import ROOT

FPS = 30
MOVING = ("photo", "illustration")


@dataclass
class Segment:
    id: str
    kind: str
    file: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def build_timeline(timings: dict, cues: list[dict], tail: float = 0.6) -> list[Segment]:
    """One segment per visual: from its first sentence's start to the next visual's start."""
    sentences = timings["sentences"]
    total = float(timings["duration"]) + tail
    starts = []
    for i, c in enumerate(cues):
        idx = [s for s in c.get("sentences", []) if 0 <= s < len(sentences)]
        starts.append(0.0 if i == 0 or not idx else float(sentences[min(idx)]["start"]))
    # Keep times increasing even if a cue lost its sentences.
    for i in range(1, len(starts)):
        starts[i] = max(starts[i], starts[i - 1] + 0.5)
    segs = []
    for i, c in enumerate(cues):
        end = starts[i + 1] if i + 1 < len(cues) else max(total, starts[i] + 1.0)
        segs.append(Segment(c["id"], c.get("kind", "text"), c.get("file") or f"{c['id']}.png",
                            round(starts[i], 3), round(end, 3)))
    return segs


def end_card(out: Path, cfg: dict) -> Path:
    """Logo, a thank-you line and a subscribe prompt; the right half stays clear for YouTube end screens."""
    img = Image.new("RGB", (W, H), COLORS["bg2"])
    d = ImageDraw.Draw(img)
    top, bottom = Image.new("RGB", (1, 1), COLORS["bg1"]).getpixel((0, 0)), Image.new("RGB", (1, 1), COLORS["bg2"]).getpixel((0, 0))
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    logo_path = ROOT / "assets" / "brand" / "logo.png"
    if logo_path.exists():
        logo = Image.open(logo_path).convert("RGBA").resize((380, 380))
        img.paste(logo, (250, 210), logo)
    big = ImageFont.truetype(font_path(True), 72)
    small = ImageFont.truetype(font_path(False), 44)
    d.text((250, 640), cfg.get("end_title", "Thanks for watching"), font=big, fill=COLORS["ink"])
    d.rounded_rectangle([(250, 740), (430, 752)], radius=6, fill=COLORS["warm"])
    y = 790
    for ln in textwrap.wrap(cfg.get("end_sub", "Subscribe for more on how things work."), 40):
        d.text((250, y), ln, font=small, fill=COLORS["cool"])
        y += 58
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def _ff(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def render_clip(seg: Segment, image: Path, out: Path, xfade: float, zoom: float, flip: bool) -> Path:
    """One visual as a silent clip, padded by the cross-fade length so fades don't eat its time."""
    length = seg.duration + xfade
    frames = max(1, round(length * FPS))
    if seg.kind in MOVING and zoom > 0:
        # zoompan makes frames from one still; working at 2x size avoids the shaky look.
        z = f"1+{zoom}*on/{frames}" if not flip else f"1+{zoom}-{zoom}*on/{frames}"
        vf = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
              f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
              f"format=yuv420p")
        _ff(["-i", str(image), "-vf", vf, "-frames:v", str(frames),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])
    else:
        vf = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={FPS},format=yuv420p"
        _ff(["-loop", "1", "-i", str(image), "-vf", vf, "-frames:v", str(frames), "-tune", "stillimage",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])
    return out


def _music_file(cfg: dict) -> Path | None:
    name = (cfg.get("music") or "").strip()
    if not name:
        return None
    p = ROOT / "assets" / "music" / name
    if not p.exists():
        print(f"[warn] music file {p} not found; making the video without music")
        return None
    return p


def _srt_path_for_filter(p: Path) -> str:
    return str(p).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def assemble(ep: Path, audio: Path, visuals_dir: Path, out_dir: Path) -> Path:
    cfg = config.load().get("assemble", {})
    xfade = float(cfg.get("crossfade", 0.4))
    zoom = float(cfg.get("ken_burns_zoom", 0.06))
    timings = json.loads((ep / "timings.json").read_text(encoding="utf-8"))
    cues = json.loads((ep / "visuals.json").read_text(encoding="utf-8"))
    segs = build_timeline(timings, cues, float(cfg.get("tail_seconds", 0.6)))

    end_len = float(cfg.get("end_card_seconds", 6))
    work = out_dir / "clips"
    work.mkdir(parents=True, exist_ok=True)
    if end_len > 0:
        end_img = end_card(work / "end_card.png", cfg)
        segs.append(Segment("END", "text", str(end_img), segs[-1].end, segs[-1].end + end_len))

    clips = []
    for i, s in enumerate(segs):
        img = Path(s.file) if s.id == "END" else visuals_dir / s.file
        if not img.exists():
            print(f"[warn] {s.id}: image {img.name} missing; using a text card")
            img = imagery.text_card(s.id, "", work / f"{s.id}_placeholder.png")
        pad = xfade if i < len(segs) - 1 else 0.0
        clips.append(render_clip(s, img, work / f"{s.id}.mp4", pad, zoom, flip=i % 2 == 1))
        print(f"{s.id} [{s.kind}] {s.start:7.2f}s - {s.end:7.2f}s ({s.duration:.1f}s)")

    total = segs[-1].end
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]
    n = len(clips)
    inputs += ["-i", str(audio)]
    a_idx = n
    wm = ROOT / "assets" / "brand" / "watermark.png"
    inputs += ["-i", str(wm)]
    wm_idx = n + 1
    music = _music_file(cfg)
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]

    # Cross-fade chain: the k-th fade starts exactly where visual k+1 begins.
    parts, last = [], "[0:v]"
    for k in range(1, n):
        lab = f"[x{k}]"
        parts.append(f"{last}[{k}:v]xfade=transition=fade:duration={xfade}:offset={segs[k].start:.3f}{lab}")
        last = lab
    size = int(cfg.get("watermark_px", 110))
    opacity = float(cfg.get("watermark_opacity", 0.6))
    parts.append(f"[{wm_idx}:v]scale={size}:{size},format=rgba,colorchannelmixer=aa={opacity}[wm]")
    parts.append(f"{last}[wm]overlay=W-w-40:H-h-40:enable='lt(t,{segs[-1].start:.3f})'[vw]")
    vlast = "[vw]"
    srt = ep / "captions.srt"
    if cfg.get("burn_captions") and srt.exists():
        style = ("FontName=DejaVu Sans,FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                 "BorderStyle=3,Outline=0,Shadow=0,BackColour=&H90000000,MarginV=60")
        parts.append(f"{vlast}subtitles='{_srt_path_for_filter(srt)}':force_style='{style}'[vs]")
        vlast = "[vs]"
    parts.append(f"{vlast}format=yuv420p[v]")

    parts.append(f"[{a_idx}:a]aresample=48000,apad=whole_dur={total:.3f}[voice]")
    if music:
        vol = float(cfg.get("music_volume", 0.12))
        parts.append(f"[{wm_idx + 1}:a]aresample=48000,volume={vol},atrim=0:{total:.3f},"
                     f"afade=t=in:d=2,afade=t=out:st={max(0, total - 3):.3f}:d=3[bed]")
        parts.append("[voice]asplit[voice1][key]")
        parts.append("[bed][key]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[ducked]")
        parts.append("[voice1][ducked]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        parts.append("[voice]anull[a]")

    out = out_dir / "video.mp4"
    _ff([*inputs, "-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]",
         "-t", f"{total:.3f}", "-r", str(FPS), "-c:v", "libx264", "-preset", cfg.get("preset", "medium"),
         "-crf", str(cfg.get("crf", 20)), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])

    (out_dir / "timeline.json").write_text(json.dumps([asdict(s) for s in segs], indent=1) + "\n", encoding="utf-8")
    limit = float(config.load()["script"].get("max_video_seconds", 300))
    mins = f"{int(total // 60)}:{int(total % 60):02d}"
    print(f"Done: {out} ({mins}, {len(segs)} visuals{', with music' if music else ''})")
    if total > limit:
        print(f"[warn] the video is {mins}, longer than the {int(limit // 60)}:{int(limit % 60):02d} target")
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("episode", type=Path)
    ap.add_argument("--audio", type=Path, required=True)
    ap.add_argument("--visuals", type=Path, required=True, help="folder with V01.png ...")
    ap.add_argument("--out", type=Path, default=ROOT / "out")
    a = ap.parse_args(argv)
    ep = a.episode if a.episode.is_absolute() else ROOT / a.episode
    assemble(ep, a.audio, a.visuals, a.out)


if __name__ == "__main__":
    main()
