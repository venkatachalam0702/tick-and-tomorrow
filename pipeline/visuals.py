"""Stage 5: make one visual per cue in the approved script, and check each with a vision model.

Usage:
  python -m pipeline.visuals make episodes/<episode> [--out out/<episode>]

Writes the images to out/<episode>/visuals/V01.png ... plus contact_sheet.jpg (media, not committed),
and to the episode folder: visuals.json (cue list, specs, checks) and credits.json (photo credits).

Gemini budget: 1 Flash call per ~6 assets (plan) + 1 Flash-Lite vision check per attempt
(+1 Flash-Lite call to redraw a failed diagram).
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

from . import config, diagram, gemini, imagery
from .brand import COLORS, font_path
from .config import ROOT
from .script import parse_script_md

KINDS = ("diagram", "photo", "illustration", "text")
PLAN_TOKENS = 12_000   # one batch of ~6 assets is ~2-4k tokens; this stops a looping reply
REDRAW_TOKENS = 4_000


@dataclass
class Cue:
    id: str
    kind: str
    description: str
    sentences: list[int]
    narration: str
    file: str = ""
    status: str = "pending"          # ok | flagged
    attempts: int = 0
    plan: dict = field(default_factory=dict)
    checks: list[dict] = field(default_factory=list)
    source: dict = field(default_factory=dict)


def cues_from_script(md: str) -> list[Cue]:
    """Group sentences under the visual that is on screen while they are spoken."""
    cues: list[Cue] = []
    idx = 0
    for sec in parse_script_md(md).sections:
        for ln in sec.lines:
            if ln.visual_type != "same" or not cues:
                kind = ln.visual_type if ln.visual_type in KINDS else "text"
                desc = ln.visual if ln.visual_type in KINDS else ln.text
                cues.append(Cue(f"V{len(cues) + 1:02d}", kind, desc, [], ""))
            cues[-1].sentences.append(idx)
            cues[-1].narration = (cues[-1].narration + " " + ln.text).strip()
            idx += 1
    return cues


# ------------------------------------------------------------------ planning (1 Flash call)

PLAN_SCHEMA = {
    "type": "OBJECT",
    "properties": {"assets": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING"},
            "diagram": diagram.DIAGRAM_SCHEMA,
            "search": {"type": "STRING"}, "prompt": {"type": "STRING"},
            "headline": {"type": "STRING"}, "sub": {"type": "STRING"},
        },
        "required": ["id"],
    }}},
    "required": ["assets"],
}


def _plan_batch(batch: list[Cue], title: str, cfg: dict, template: str) -> dict:
    listing = "\n\n".join(f"{c.id} [{c.kind}] {c.description}\n   narration: \"{c.narration[:400]}\"" for c in batch)
    # Plain replace, not str.format: the prompt holds JSON examples with braces.
    prompt = (template.replace("{channel}", cfg["channel"]["name"]).replace("{title}", title)
              .replace("{cues}", listing))
    ids = ", ".join(c.id for c in batch)
    try:
        data = gemini.generate(prompt, role="writer", json_schema=PLAN_SCHEMA, temperature=0.4,
                               purpose=f"visual plan {ids}", max_tokens=PLAN_TOKENS)
    except gemini.GeminiError as e:
        if "rejected" not in str(e):
            raise
        # Some models refuse deeply nested schemas: ask for plain JSON with the same shape instead.
        print(f"[warn] schema refused, retrying without it: {str(e)[:200]}")
        data = gemini.generate(prompt + '\n\nReply with JSON only: {"assets": [{"id": "V01", ...}]}',
                               role="writer", json_schema=True, temperature=0.4,
                               purpose=f"visual plan {ids} (no schema)", max_tokens=PLAN_TOKENS)
    return {a.get("id"): a for a in (data or {}).get("assets", []) if isinstance(a, dict)}


def plan(cues: list[Cue], title: str) -> None:
    """Plan in small batches: short replies are faster, rarely time out, and one bad reply costs little."""
    cfg = config.load()
    template = (ROOT / "prompts" / "visuals_plan.md").read_text(encoding="utf-8")
    size = int(cfg["visuals"].get("plan_batch", 6))
    for start in range(0, len(cues), size):
        batch = cues[start:start + size]
        try:
            by_id = _plan_batch(batch, title, cfg, template)
        except gemini.GeminiError as e:  # keep going: these cues fall back to their descriptions
            print(f"[warn] planning {batch[0].id}-{batch[-1].id} failed: {str(e)[:200]}")
            by_id = {}
        for c in batch:
            c.plan = by_id.get(c.id, {})


# ------------------------------------------------------------------ vision check

CHECK_SCHEMA = {
    "type": "OBJECT",
    "properties": {"pass": {"type": "BOOLEAN"}, "score": {"type": "INTEGER"},
                   "issues": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["pass", "score", "issues"],
}


def vision_check(c: Cue, path: Path) -> dict:
    prompt = (ROOT / "prompts" / "visuals_check.md").read_text(encoding="utf-8").format(
        kind=c.kind, cue=c.description, narration=c.narration[:600])
    # Send a smaller JPEG: cheaper and well within what the check needs.
    img = Image.open(path).convert("RGB")
    img.thumbnail((960, 540))
    import io

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    try:
        r = gemini.generate(prompt, role="helper", json_schema=CHECK_SCHEMA, temperature=0,
                            purpose=f"check {c.id}", images=[(buf.getvalue(), "image/jpeg")])
    except gemini.GeminiError as e:
        return {"pass": False, "score": 0, "issues": [f"vision check unavailable: {e}"[:300]], "error": True}
    return {"pass": bool(r.get("pass")), "score": int(r.get("score", 0)), "issues": list(r.get("issues", []))}


def passed(check: dict, min_score: int) -> bool:
    return check.get("pass") and check.get("score", 0) >= min_score


# ------------------------------------------------------------------ makers

REDRAW_SCHEMA = diagram.DIAGRAM_SCHEMA


def _diagram_rules() -> str:
    text = (ROOT / "prompts" / "visuals_plan.md").read_text(encoding="utf-8")
    return text.split("- diagram:", 1)[1].split("- photo:", 1)[0].strip()


def _redraw(c: Cue, problems: list[str]) -> dict:
    prompt = (f"Fix a diagram layout for a science explainer video.\n\nWhat it must show: {c.description}\n"
              f"Narration over it: \"{c.narration[:500]}\"\n\nProblems found in the last version:\n"
              + "\n".join(f"- {p}" for p in problems)
              + f"\n\nLast layout JSON:\n{json.dumps(c.plan.get('diagram', {}))}\n\n"
              f"Layout rules:\n{_diagram_rules()}\n\nReturn the corrected layout (title, elements) in the compact "
              f"{{type, at, label, color}} form. Keep it to 14 elements or fewer.")
    return gemini.generate(prompt, role="helper", json_schema=REDRAW_SCHEMA, temperature=0.3,
                           purpose=f"redraw {c.id}", max_tokens=REDRAW_TOKENS)


def make_one(c: Cue, out_dir: Path, cfg: dict) -> None:
    out = out_dir / f"{c.id}.png"
    c.file = out.name
    kind = c.kind
    photos: list[dict] | None = None
    problems: list[str] = []
    for attempt in range(1, cfg["max_attempts"] + 1):
        c.attempts = attempt
        try:
            if kind == "diagram":
                if attempt > 1 and problems:
                    c.plan["diagram"] = _redraw(c, problems)
                spec = c.plan.get("diagram") or {}
                if not spec.get("elements"):
                    raise diagram.DiagramError("no diagram layout from the planner")
                diagram.render(spec, out)
                c.source = {"type": "diagram", "made_by": "code (matplotlib), layout by Gemini"}
            elif kind == "photo":
                if photos is None:
                    photos = imagery.photo_candidates(c.plan.get("search") or imagery.keywords(c.description, 5))
                if not photos:
                    if cfg.get("photo_fallback") == "illustration":
                        print(f"{c.id}: no licensed photo found; drawing an illustration instead")
                        kind = "illustration"
                        c.source = {"note": "no licensed photo found"}
                        imagery.flux_illustration(c.plan.get("prompt") or c.description, out, seed=attempt)
                        c.source = {"type": "illustration", "made_by": "FLUX.1 schnell (Cloudflare Workers AI)",
                                    "note": "photo fallback"}
                    else:
                        raise RuntimeError("no licensed photo found")
                else:
                    pick = photos.pop(0)
                    imagery.download_photo(pick, out)
                    c.source = {"type": "photo", **{k: pick[k] for k in ("title", "author", "license", "page", "provider")}}
            elif kind == "illustration":
                imagery.flux_illustration(c.plan.get("prompt") or c.description, out,
                                          seed=random.randint(1, 10_000) if attempt > 1 else 7)
                c.source = {"type": "illustration", "made_by": "FLUX.1 schnell (Cloudflare Workers AI)"}
            else:
                imagery.text_card(c.plan.get("headline") or c.description[:60], c.plan.get("sub", ""), out)
                c.source = {"type": "text", "made_by": "code (Pillow)"}
        except Exception as e:  # noqa: BLE001 - record and retry
            problems = [f"could not be made: {e}"[:300]]
            c.checks.append({"attempt": attempt, "pass": False, "score": 0, "issues": problems})
            print(f"{c.id} attempt {attempt}: {problems[0]}")
            continue

        check = vision_check(c, out)
        check["attempt"] = attempt
        c.checks.append(check)
        print(f"{c.id} attempt {attempt} [{kind}]: score {check['score']}, pass={check['pass']} {check['issues'][:2]}")
        if passed(check, cfg["min_qa_score"]):
            c.status = "ok"
            return
        if check.get("error"):  # checker down: keep the image, flag it for the human
            break
        problems = check["issues"] or ["did not pass the vision check"]
    c.status = "flagged" if out.exists() else "missing"
    if not out.exists():  # never leave a hole in the timeline
        imagery.text_card(c.plan.get("headline") or c.description[:60], "", out)
        c.source = {"type": "text", "made_by": "code (Pillow)", "note": "placeholder after failed attempts"}


def contact_sheet(cues: list[Cue], out_dir: Path) -> Path:
    cols, tw, th = 4, 480, 270
    rows = (len(cues) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * (th + 40)), COLORS["bg2"])
    d = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(font_path(True), 22)
    for i, c in enumerate(cues):
        x, y = (i % cols) * tw, (i // cols) * (th + 40)
        p = out_dir / c.file
        if p.exists():
            im = Image.open(p).convert("RGB")
            im.thumbnail((tw - 8, th - 8))
            sheet.paste(im, (x + 4, y + 4))
        mark = "OK" if c.status == "ok" else "CHECK"
        d.text((x + 8, y + th + 6), f"{c.id} {c.kind} {mark}", font=font,
               fill=COLORS["cool"] if c.status == "ok" else COLORS["warm"])
    path = out_dir.parent / "contact_sheet.jpg"
    sheet.save(path, quality=85)
    return path


def run(ep: Path, out: Path) -> list[Cue]:
    cfg = config.load()["visuals"]
    topic = yaml.safe_load((ep / "topic.yaml").read_text(encoding="utf-8"))
    cues = cues_from_script((ep / "script.md").read_text(encoding="utf-8"))
    print(f"{len(cues)} visuals to make: " + ", ".join(f"{k} {sum(c.kind == k for c in cues)}" for k in KINDS))
    plan(cues, topic["title"])
    vis_dir = out / "visuals"
    vis_dir.mkdir(parents=True, exist_ok=True)
    for c in cues:
        make_one(c, vis_dir, cfg)
    sheet = contact_sheet(cues, vis_dir)
    (ep / "visuals.json").write_text(json.dumps([asdict(c) for c in cues], indent=1, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
    credits = [c.source | {"visual": c.id} for c in cues if c.source.get("type") == "photo"]
    (ep / "credits.json").write_text(json.dumps(credits, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    flagged = [c for c in cues if c.status != "ok"]
    print(f"Done: {len(cues) - len(flagged)} ok, {len(flagged)} flagged for review. Contact sheet: {sheet}")
    for c in flagged:
        last = c.checks[-1]["issues"] if c.checks else []
        print(f"  {c.id} ({c.kind}): {'; '.join(last)[:200]}")
    print("Gemini usage:", gemini.usage_summary())
    return cues


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make"])
    ap.add_argument("episode", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    ep = a.episode if a.episode.is_absolute() else ROOT / a.episode
    run(ep, a.out or ROOT / "out" / ep.name)


if __name__ == "__main__":
    main()
