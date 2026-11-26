"""Stage 1: suggest topics for Gate 1.

Usage:
  python -m pipeline.topics suggest --out issue.md
Writes the Gate 1 issue body (a numbered checklist). Venkat approves on the issue with
  /approve 2          pick idea number 2
  /approve            pick the single ticked box
  /topic How do lasers work? | Laser     a topic of his own (Wikipedia article optional)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from . import config, wikipedia
from .config import ROOT
from .util import today

MARKER = "<!-- tt:gate1 -->"


def used_titles() -> set[str]:
    used = set()
    for f in (ROOT / "episodes").glob("*/topic.yaml"):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if data.get("title"):
            used.add(data["title"].strip().lower())
    return used


def load_backlog() -> list[dict]:
    path = ROOT / config.load()["topics"]["backlog_file"]
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("ideas", [])


def rank(ideas: list[dict], used: set[str], days: int, views=wikipedia.pageviews) -> list[dict]:
    fresh = [dict(i) for i in ideas if i["title"].strip().lower() not in used]
    for i in fresh:
        i["views"] = views(i["wiki"], days) if i.get("wiki") else 0
    return sorted(fresh, key=lambda i: (not i.get("pinned", False), -i["views"]))


def render_issue(top: list[dict], days: int) -> str:
    lines = [
        MARKER,
        f"**Gate 1: pick this week's How Things Work topic** ({today()})",
        "",
        f"Ranked by Wikipedia pageviews over the last {days} days (a rough signal of public interest).",
        "",
    ]
    for n, i in enumerate(top, 1):
        wiki = i.get("wiki", "")
        link = f"https://en.wikipedia.org/wiki/{wiki.replace(' ', '_')}" if wiki else ""
        pin = " 📌" if i.get("pinned") else ""
        lines.append(f"- [ ] **{n}. {i['title']}**{pin} · [{wiki}]({link}) · {i['views']:,} views")
        lines.append(f"  <!-- tt:topic {n} | {i['title']} | {wiki} -->")
    lines += [
        "",
        "### How to approve (from the GitHub app is fine)",
        "Add a comment on this issue:",
        "- `/approve 1` to pick idea 1 (any number works), or tick one box above and comment `/approve`",
        "- `/topic How do lasers work? | Laser` for your own topic (the part after `|` is the main Wikipedia article, optional)",
        "",
        "Research then starts automatically and opens a fact-check pull request (Gate 2) in about 5 minutes.",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("suggest")
    s.add_argument("--out", default="issue.md")
    args = ap.parse_args(argv)

    cfg = config.load()["topics"]
    days = cfg["pageview_days"]
    ranked = rank(load_backlog(), used_titles(), days)
    if not ranked:
        raise SystemExit("Backlog is empty: add ideas to topics/backlog.yaml")
    top = ranked[: cfg["suggestions_per_issue"]]
    Path(args.out).write_text(render_issue(top, days), encoding="utf-8")
    print(f"Wrote {args.out} with {len(top)} ideas.")


if __name__ == "__main__":
    main()
