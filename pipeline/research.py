"""Stage 2: research an approved topic and write the Gate 2 fact sheet.

Usage:
  python -m pipeline.research --title "How does a microwave work?" [--wiki "Microwave oven"]
         [--url https://extra.source/page ...] [--episodes-dir episodes]

Writes episodes/<date>-<slug>/ with topic.yaml, sources/S*.md, facts.md, facts.json
and prints the episode folder path on the last line.

Gemini budget: 1 Flash-Lite call (pick articles) + 1 Flash call (fact sheet).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

import yaml

from . import config, gemini, wikipedia
from .config import ROOT
from .facts import Source, render_facts_md, verify
from .util import http, slugify, today

SELECT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "wikipedia_titles": {"type": "ARRAY", "items": {"type": "STRING"}},
        "key_questions": {"type": "ARRAY", "items": {"type": "STRING"}},
        "angle": {"type": "STRING"},
    },
    "required": ["wikipedia_titles", "key_questions", "angle"],
}

FACTS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "claims": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "section": {"type": "STRING"},
                    "claim": {"type": "STRING"},
                    "evidence": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {"source_id": {"type": "STRING"}, "quote": {"type": "STRING"}},
                            "required": ["source_id", "quote"],
                        },
                    },
                },
                "required": ["section", "claim", "evidence"],
            },
        }
    },
    "required": ["claims"],
}


def _prompt(name: str, **kw) -> str:
    return (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8").format(**kw)


def plan(title: str, wiki: str, max_articles: int) -> dict:
    """One Flash-Lite call: which articles, which questions, what angle. Falls back to search."""
    try:
        p = gemini.generate(_prompt("research_select", title=title, wiki=wiki, max_articles=max_articles),
                            role="helper", json_schema=SELECT_SCHEMA, purpose="pick sources")
    except gemini.GeminiError as e:
        print(f"[warn] planning call failed, using Wikipedia search only: {e}")
        p = {"wikipedia_titles": [], "key_questions": [], "angle": ""}
    candidates = ([wiki] if wiki else []) + list(p.get("wikipedia_titles", []))
    titles = wikipedia.resolve_titles(candidates)
    if not titles:
        titles = wikipedia.search(title, limit=2)
    p["wikipedia_titles"] = titles[:max_articles]
    if not p.get("key_questions"):
        p["key_questions"] = [title]
    return p


def domain_ok(url: str, rcfg: dict) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host or any(host == d or host.endswith("." + d) for d in rcfg["blocked_domains"]):
        return False
    if url.lower().split("?")[0].endswith((".jpg", ".png", ".gif", ".zip", ".mp4")):
        return False
    return any(host.endswith(s) if s.startswith(".") else (host == s or host.endswith("." + s))
               for s in rcfg["trusted_domain_suffixes"])


def fetch_page(url: str) -> tuple[str, str]:
    """Return (title, clean text) of a web page or PDF-free HTML page, '' on failure."""
    import trafilatura

    try:
        r = http().get(url, timeout=30)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return "", ""
        text = trafilatura.extract(r.text, include_comments=False, include_tables=True) or ""
        meta = trafilatura.extract_metadata(r.text)
        return (meta.title if meta and meta.title else urlparse(url).hostname or url), text
    except Exception as e:  # any site can misbehave; research continues without it
        print(f"[warn] could not fetch {url}: {e}")
        return "", ""


def gather_sources(titles: list[str], extra_urls: list[str], rcfg: dict) -> dict[str, Source]:
    sources: dict[str, Source] = {}
    ref_links: list[str] = []

    def add(title, url, text, kind):
        sid = f"S{len(sources) + 1}"
        sources[sid] = Source(sid, title, url, text[: rcfg["max_chars_per_source"]], kind, today())

    for t in titles:
        a = wikipedia.article(t)
        if len(a["text"]) >= rcfg["min_source_chars"]:
            add(f"{a['title']} (Wikipedia)", a["url"], a["text"], "wikipedia")
            ref_links += [u for u in a["extlinks"] if domain_ok(u, rcfg)]

    for url in extra_urls:
        title, text = fetch_page(url)
        if len(text) >= rcfg["min_source_chars"]:
            add(title, url, text, "provided")

    taken = 0
    for url in dict.fromkeys(ref_links):  # de-duplicate, keep order
        if taken >= rcfg["max_reference_sources"]:
            break
        title, text = fetch_page(url)
        if len(text) >= rcfg["min_source_chars"]:
            add(title, url, text, "reference")
            taken += 1
    return sources


def sources_block(sources: dict[str, Source], max_total: int) -> str:
    parts, total = [], 0
    for s in sources.values():
        chunk = f"=== {s.id}: {s.title} ===\n{s.text}\n"
        if total + len(chunk) > max_total:
            chunk = chunk[: max(0, max_total - total)]
        parts.append(chunk)
        total += len(chunk)
    return "\n".join(parts)


def write_episode(ep: Path, title: str, wiki: str, p: dict, sources: dict[str, Source],
                  raw_claims: list[dict]) -> None:
    claims = verify(raw_claims, sources)
    (ep / "sources").mkdir(parents=True, exist_ok=True)
    for s in sources.values():
        header = f"---\nid: {s.id}\ntitle: {json.dumps(s.title)}\nurl: {s.url}\nkind: {s.kind}\nfetched: {s.fetched}\n---\n\n"
        (ep / "sources" / f"{s.id}.md").write_text(header + s.text + "\n", encoding="utf-8")
    topic = {"title": title, "wiki": wiki, "pillar": "How Things Work", "created": today(),
             "angle": p.get("angle", ""), "key_questions": p.get("key_questions", []),
             "status": "gate-2-factcheck"}
    (ep / "topic.yaml").write_text(yaml.safe_dump(topic, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (ep / "facts.json").write_text(json.dumps(
        [{"id": c.id, "section": c.section, "claim": c.text, "status": c.status,
          "evidence": c.evidence, "rejected": c.rejected} for c in claims],
        indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (ep / "facts.md").write_text(render_facts_md(title, claims, sources, gemini.usage_summary()), encoding="utf-8")
    kept = sum(c.status != "dropped" for c in claims)
    print(f"Claims: {len(claims)} proposed, {kept} kept after quote check.")
    if kept == 0:
        raise SystemExit("No claim survived the quote check; not opening a PR.")


def run(title: str, wiki: str = "", extra_urls: list[str] | None = None,
        episodes_dir: Path | None = None) -> Path:
    cfg = config.load()
    rcfg = cfg["research"]
    episodes_dir = episodes_dir or ROOT / "episodes"
    ep = episodes_dir / f"{today()}-{slugify(title)}"
    if ep.exists():
        raise SystemExit(f"{ep} already exists. Delete it or pick another title.")

    p = plan(title, wiki, rcfg["max_wikipedia_articles"])
    print("Wikipedia articles:", ", ".join(p["wikipedia_titles"]))
    sources = gather_sources(p["wikipedia_titles"], extra_urls or [], rcfg)
    if not sources:
        raise SystemExit("No usable sources found.")
    print(f"Sources saved: {len(sources)}")

    prompt = _prompt("research_facts", channel=cfg["channel"]["name"], title=title,
                     angle=p.get("angle", ""), target_claims=rcfg["target_claims"],
                     questions="\n".join(f"- {q}" for q in p["key_questions"]),
                     sources=sources_block(sources, rcfg["max_total_chars"]))
    data = gemini.generate(prompt, role="writer", json_schema=FACTS_SCHEMA, temperature=0.2,
                           purpose="fact sheet")
    write_episode(ep, title, wiki or (p["wikipedia_titles"][0] if p["wikipedia_titles"] else ""),
                  p, sources, data.get("claims", []))
    print("Gemini usage:", gemini.usage_summary())
    print(ep.relative_to(ROOT) if ep.is_relative_to(ROOT) else ep)
    return ep


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--wiki", default="")
    ap.add_argument("--url", action="append", default=[], help="extra source URL (repeatable)")
    ap.add_argument("--episodes-dir", type=Path)
    a = ap.parse_args(argv)
    run(a.title.strip(), a.wiki.strip(), a.url, a.episodes_dir)


if __name__ == "__main__":
    main()
