"""Stage 3: write the narration script from approved facts, lint it, and render script.md (Gate 3).

Usage:
  python -m pipeline.script write episodes/<episode>      # 1 Flash call (+1 repair call if lint fails)
  python -m pipeline.script lint  episodes/<episode>/script.md [...]   # re-check after human edits

script.md is the human-editable source of truth. Between the SCRIPT markers, each spoken
sentence is one line, followed by its visual cue on the next line:

    ## Chapter: The magnetron
    Inside every microwave sits a small metal tube called a magnetron. [C04]
    > 🎬 diagram: Cutaway of a magnetron, electrons spiralling past copper cavities.

Venkat edits sentences in place on GitHub. Claim tags [Cxx] link a sentence to facts.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import config, gemini
from .config import ROOT
from .facts import parse_facts_md
from .util import normalize

KINDS = ["hook", "promise", "chapter", "recap", "question"]
SCRIPT_START, SCRIPT_END = "<!-- SCRIPT START -->", "<!-- SCRIPT END -->"

LINE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "text": {"type": "STRING"},
        "claims": {"type": "ARRAY", "items": {"type": "STRING"}},
        "visual_type": {"type": "STRING", "enum": ["diagram", "photo", "illustration", "text", "same"]},
        "visual": {"type": "STRING"},
    },
    "required": ["text", "claims", "visual_type", "visual"],
}
SCRIPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "sections": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "kind": {"type": "STRING", "enum": KINDS},
                    "title": {"type": "STRING"},
                    "lines": {"type": "ARRAY", "items": LINE_SCHEMA},
                },
                "required": ["kind", "lines"],
            },
        },
        "pronunciations": {
            "type": "ARRAY",
            "items": {"type": "OBJECT", "properties": {"term": {"type": "STRING"}, "say": {"type": "STRING"}},
                      "required": ["term", "say"]},
        },
    },
    "required": ["sections", "pronunciations"],
}

FACTUAL = re.compile(r"\d")
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")


def words(text: str) -> int:
    return len(WORD.findall(text))


# ---------------------------------------------------------------- data model

@dataclass
class Line:
    text: str
    claims: list[str] = field(default_factory=list)
    visual_type: str = "same"
    visual: str = "same"


@dataclass
class Section:
    kind: str
    title: str = ""
    lines: list[Line] = field(default_factory=list)


@dataclass
class Script:
    sections: list[Section]
    pronunciations: list[dict] = field(default_factory=list)

    @property
    def lines(self) -> list[Line]:
        return [ln for s in self.sections for ln in s.lines]

    @property
    def word_count(self) -> int:
        return sum(words(ln.text) for ln in self.lines)

    @classmethod
    def from_json(cls, data: dict) -> "Script":
        secs = []
        for s in data.get("sections", []):
            lines = [Line(ln.get("text", "").strip(), [c.strip() for c in ln.get("claims", []) if c.strip()],
                          ln.get("visual_type", "same").strip().lower(), ln.get("visual", "same").strip())
                     for ln in s.get("lines", []) if ln.get("text", "").strip()]
            secs.append(Section(s.get("kind", "").strip().lower(), s.get("title", "").strip(), lines))
        return cls(secs, [p for p in data.get("pronunciations", []) if p.get("term")])

    def to_json(self) -> dict:
        return {"sections": [{"kind": s.kind, "title": s.title,
                              "lines": [ln.__dict__ for ln in s.lines]} for s in self.sections],
                "pronunciations": self.pronunciations}


# ---------------------------------------------------------------- lint

@dataclass
class LintResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _shingles(text: str, n: int) -> set[tuple[str, ...]]:
    toks = WORD.findall(normalize(text))
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def lint(script: Script, claim_ids: set[str], source_texts: list[str] | None = None,
         cfg: dict | None = None) -> LintResult:
    cfg = cfg or config.load()["script"]
    r = LintResult()
    kinds = [s.kind for s in script.sections]

    # Structure: hook, promise, chapters, recap, question.
    expected_shape = re.fullmatch(r"hook promise( chapter)+ recap question", " ".join(kinds))
    if not expected_shape:
        r.errors.append(f"Sections must be hook, promise, {cfg['chapters_min']}-{cfg['chapters_max']} chapters, "
                        f"recap, question (got: {', '.join(kinds) or 'none'}).")
    n_ch = kinds.count("chapter")
    if not cfg["chapters_min"] <= n_ch <= cfg["chapters_max"]:
        r.errors.append(f"{n_ch} chapters; need {cfg['chapters_min']} to {cfg['chapters_max']}.")
    for s in script.sections:
        if s.kind == "chapter" and not s.title:
            r.errors.append("Every chapter needs a title.")
        if not s.lines:
            r.errors.append(f"Section '{s.title or s.kind}' is empty.")

    # Length.
    wc = script.word_count
    if not cfg["words_min"] <= wc <= cfg["words_max"]:
        r.errors.append(f"{wc} words; need {cfg['words_min']} to {cfg['words_max']} "
                        f"(about {cfg['words_min'] // cfg['words_per_minute']}-{cfg['words_max'] // cfg['words_per_minute']} minutes).")
    hook = next((s for s in script.sections if s.kind == "hook"), None)
    if hook:
        hw = sum(words(ln.text) for ln in hook.lines)
        if hw > cfg["hook_max_words"]:
            r.errors.append(f"Hook is {hw} words; keep it to {cfg['hook_max_words']} (first ~15 seconds).")

    # Claims, visuals, wording, line by line.
    banned = [b.lower() for b in cfg["banned_phrases"]]
    first_visual = True
    for s in script.sections:
        for ln in s.lines:
            short = ln.text if len(ln.text) < 70 else ln.text[:67] + "..."
            unknown = [c for c in ln.claims if c not in claim_ids]
            if unknown:
                r.errors.append(f"Unknown claim id {', '.join(unknown)} in: \"{short}\" (not in facts.md).")
            if s.kind == "chapter" and not ln.claims and FACTUAL.search(ln.text):
                r.errors.append(f"Sentence with a number has no claim tag: \"{short}\"")
            elif s.kind == "chapter" and not ln.claims:
                r.warnings.append(f"Untagged chapter sentence (fine if it is a transition or analogy): \"{short}\"")
            if ln.visual_type not in cfg["visual_types"]:
                r.errors.append(f"Unknown visual type '{ln.visual_type}' for: \"{short}\"")
            if first_visual and ln.visual_type == "same":
                r.errors.append("The first sentence needs its own visual, not 'same'.")
            first_visual = False
            if ln.visual_type != "same" and not ln.visual.strip():
                r.errors.append(f"Missing visual description for: \"{short}\"")
            low = ln.text.lower()
            for b in banned:
                if re.search(rf"\b{re.escape(b)}\b", low):
                    r.errors.append(f"Filler phrase \"{b}\" in: \"{short}\"")
            if words(ln.text) > 35:
                r.warnings.append(f"Long sentence ({words(ln.text)} words), hard to narrate: \"{short}\"")

    # Originality: long runs copied from the sources.
    if source_texts:
        n = cfg["copied_run_words"]
        src = set().union(*(_shingles(t, n) for t in source_texts))
        for ln in script.lines:
            if _shingles(ln.text, n) & src:
                short = ln.text if len(ln.text) < 70 else ln.text[:67] + "..."
                r.errors.append(f"Copies {n}+ words straight from a source; rephrase: \"{short}\"")

    # Visual variety: a still held too long feels like a slideshow.
    run = 0
    for ln in script.lines:
        run = run + 1 if ln.visual_type == "same" else 0
        if run == 6:
            r.warnings.append("Six or more sentences in a row share one visual; consider a new one.")
    return r


# ---------------------------------------------------------------- markdown

_HEAD = {"hook": "Hook", "promise": "Promise", "chapter": "Chapter", "recap": "Recap", "question": "Question"}
_TAGS = re.compile(r"\s*\[((?:C\d+\s*,?\s*)+)\]\s*$")
_VISUAL = re.compile(r"^>\s*🎬\s*(\w+)\s*:\s*(.*)$")


def render_script_md(title: str, script: Script, result: LintResult, cfg: dict | None = None) -> str:
    cfg = cfg or config.load()["script"]
    wc = script.word_count
    minutes = wc / cfg["words_per_minute"]
    status = "✅ all checks pass" if result.ok else f"❌ {len(result.errors)} problem(s) to fix"
    out = [
        f"# Script: {title}",
        "",
        f"**{wc} words · about {minutes:.1f} minutes · {status}**",
        "",
        "## How to review (Gate 3)",
        "1. Read it aloud in your head: does the hook make you want to keep watching?",
        "2. Edit any sentence in place (pencil icon on GitHub). Keep one sentence per line.",
        "3. Keep the `[C04]` tags on factual sentences; they point to `facts.md`.",
        "4. The `> 🎬` line under a sentence is the visual. Change the words freely; the type must be "
        "diagram, photo, illustration, text or same.",
        "5. Merge the pull request to approve. The checks re-run on every edit you save.",
        "",
    ]
    if result.errors or result.warnings:
        out += ["<details><summary>Automatic checks</summary>", ""]
        out += [f"- ❌ {e}" for e in result.errors] + [f"- ⚠️ {w}" for w in result.warnings]
        out += ["", "</details>", ""]
    out.append(SCRIPT_START)
    for s in script.sections:
        head = _HEAD.get(s.kind, s.kind.title())
        out += ["", f"## {head}: {s.title}" if s.title else f"## {head}", ""]
        for ln in s.lines:
            tags = f" [{', '.join(ln.claims)}]" if ln.claims else ""
            out.append(f"{ln.text}{tags}")
            out.append(f"> 🎬 {ln.visual_type}: {ln.visual}")
    out += ["", SCRIPT_END, "", "## Pronunciation", ""]
    out += [f"- {p['term']}: {p['say']}" for p in script.pronunciations] or ["- (none)"]
    return "\n".join(out) + "\n"


def parse_script_md(md: str) -> Script:
    """Read a (possibly human-edited) script.md back into a Script."""
    if SCRIPT_START not in md or SCRIPT_END not in md:
        raise ValueError("script.md is missing the SCRIPT START/END markers.")
    body = md.split(SCRIPT_START, 1)[1].split(SCRIPT_END, 1)[0]
    sections: list[Section] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("## "):
            head, _, title = line[3:].partition(":")
            kind = next((k for k, v in _HEAD.items() if v.lower() == head.strip().lower()), head.strip().lower())
            sections.append(Section(kind, title.strip()))
            continue
        m = _VISUAL.match(line)
        if m:
            if sections and sections[-1].lines:
                sections[-1].lines[-1].visual_type = m.group(1).lower()
                sections[-1].lines[-1].visual = m.group(2).strip()
            continue
        if not sections:
            sections.append(Section("hook"))
        tags = _TAGS.search(line)
        claims = [c.strip() for c in tags.group(1).split(",") if c.strip()] if tags else []
        text = _TAGS.sub("", line).strip()
        sections[-1].lines.append(Line(text, claims, "same", "same"))
    pron = []
    if "## Pronunciation" in md:
        for raw in md.split("## Pronunciation", 1)[1].splitlines():
            m = re.match(r"^-\s*(.+?):\s*(.+)$", raw.strip())
            if m and m.group(1) != "(none)":
                pron.append({"term": m.group(1).strip(), "say": m.group(2).strip()})
    return Script(sections, pron)


# ---------------------------------------------------------------- generation

def _sources(ep: Path) -> list[str]:
    texts = []
    for f in sorted((ep / "sources").glob("S*.md")):
        t = f.read_text(encoding="utf-8")
        texts.append(t.split("\n---\n", 1)[1] if t.startswith("---") and "\n---\n" in t else t)
    return texts


def _quotes(ep: Path) -> list[str]:
    """Quotes from facts.json also count as source text for the copy check."""
    f = ep / "facts.json"
    if not f.exists():
        return []
    return [e["quote"] for c in json.loads(f.read_text(encoding="utf-8")) for e in c.get("evidence", [])]


def write(ep: Path) -> tuple[Script, LintResult]:
    cfg_all = config.load()
    cfg = cfg_all["script"]
    topic = yaml.safe_load((ep / "topic.yaml").read_text(encoding="utf-8"))
    facts = parse_facts_md((ep / "facts.md").read_text(encoding="utf-8"))
    if len(facts) < 5:
        raise SystemExit(f"Only {len(facts)} approved facts in facts.md; need at least 5 to write a script.")
    claim_ids = {f["id"] for f in facts}
    sources = _sources(ep) + _quotes(ep)

    prompt = (ROOT / "prompts" / "script_write.md").read_text(encoding="utf-8").format(
        channel=cfg_all["channel"]["name"], title=topic["title"], angle=topic.get("angle", ""),
        facts="\n".join(f"{f['id']}: {f['claim']}" for f in facts),
        hook_max_words=cfg["hook_max_words"], chapters_min=cfg["chapters_min"], chapters_max=cfg["chapters_max"],
        words_min=cfg["words_min"], words_max=cfg["words_max"],
        banned=", ".join(f'"{b}"' for b in cfg["banned_phrases"]))
    script = Script.from_json(gemini.generate(prompt, role="writer", json_schema=SCRIPT_SCHEMA,
                                              temperature=0.7, purpose="script"))
    result = lint(script, claim_ids, sources, cfg)
    print(f"Draft: {script.word_count} words, {len(result.errors)} error(s).")

    for n in range(cfg["repair_rounds"]):
        if result.ok:
            break
        repair = (ROOT / "prompts" / "script_repair.md").read_text(encoding="utf-8").format(
            errors="\n".join(f"- {e}" for e in result.errors), words_min=cfg["words_min"],
            words_max=cfg["words_max"], claim_ids=", ".join(sorted(claim_ids)),
            script_json=json.dumps(script.to_json(), ensure_ascii=False))
        try:
            fixed = Script.from_json(gemini.generate(repair, role="writer", json_schema=SCRIPT_SCHEMA,
                                                     temperature=0.4, purpose=f"script repair {n + 1}"))
        except gemini.GeminiError as e:
            print(f"[warn] repair call failed, keeping the draft: {e}")
            break
        fixed_result = lint(fixed, claim_ids, sources, cfg)
        print(f"Repair {n + 1}: {fixed.word_count} words, {len(fixed_result.errors)} error(s).")
        if len(fixed_result.errors) <= len(result.errors):
            script, result = fixed, fixed_result
    return script, result


def save(ep: Path, script: Script, result: LintResult) -> None:
    topic_path = ep / "topic.yaml"
    topic = yaml.safe_load(topic_path.read_text(encoding="utf-8"))
    (ep / "script.md").write_text(render_script_md(topic["title"], script, result), encoding="utf-8")
    topic["status"] = "gate-3-script"
    topic_path.write_text(yaml.safe_dump(topic, sort_keys=False, allow_unicode=True), encoding="utf-8")


def lint_file(path: Path) -> LintResult:
    ep = path.parent
    script = parse_script_md(path.read_text(encoding="utf-8"))
    ids = {f["id"] for f in parse_facts_md((ep / "facts.md").read_text(encoding="utf-8"))}
    return lint(script, ids, _sources(ep) + _quotes(ep))


def _report(path: Path, r: LintResult) -> str:
    lines = [f"**{path}**: " + ("✅ all checks pass" if r.ok else f"❌ {len(r.errors)} problem(s)")]
    lines += [f"- ❌ {e}" for e in r.errors] + [f"- ⚠️ {w}" for w in r.warnings]
    return "\n".join(lines)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("episode", type=Path)
    li = sub.add_parser("lint")
    li.add_argument("files", nargs="+", type=Path)
    a = ap.parse_args(argv)

    if a.cmd == "write":
        ep = a.episode if a.episode.is_absolute() else ROOT / a.episode
        script, result = write(ep)
        save(ep, script, result)
        print("Gemini usage:", gemini.usage_summary())
        report = _report(ep.relative_to(ROOT) / "script.md", result)
        print(report)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
                f.write(f"words={script.word_count}\nerrors={len(result.errors)}\n")
                f.write(f"report<<EOF_REPORT\n{report}\nEOF_REPORT\n")
        return

    bad = 0
    reports = []
    for f in a.files:
        r = lint_file(f)
        reports.append(_report(f, r))
        bad += len(r.errors)
    text = "\n\n".join(reports)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
