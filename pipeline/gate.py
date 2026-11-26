"""Parse approval comments on the Gate 1 issue.

Run inside GitHub Actions:
  python -m pipeline.gate resolve-topic
reads the event payload ($GITHUB_EVENT_PATH) and writes `title` and `wiki` to $GITHUB_OUTPUT.
Exits non-zero with a human-readable message (in `error`) when the comment cannot be understood.
"""
from __future__ import annotations

import json
import os
import re
import sys

TOPIC_LINE = re.compile(r"<!-- tt:topic (\d+) \| (.*?) \| (.*?) -->")
TICKED = re.compile(r"^- \[[xX]\] \*\*(\d+)\.", re.M)


class GateError(ValueError):
    pass


def options(issue_body: str) -> dict[int, tuple[str, str]]:
    return {int(n): (t.strip(), w.strip()) for n, t, w in TOPIC_LINE.findall(issue_body or "")}


def resolve_topic(comment: str, issue_body: str) -> tuple[str, str]:
    """Return (title, wikipedia_article) chosen by an approval comment."""
    first = (comment or "").strip().splitlines()[0].strip() if (comment or "").strip() else ""
    if first.lower().startswith("/topic"):
        rest = first[len("/topic"):].strip()
        if not rest:
            raise GateError("`/topic` needs a title, e.g. `/topic How do lasers work? | Laser`.")
        title, _, wiki = rest.partition("|")
        return title.strip(), wiki.strip()
    if not first.lower().startswith("/approve"):
        raise GateError("Comment must start with `/approve` or `/topic`.")
    opts = options(issue_body)
    arg = first[len("/approve"):].strip().lstrip("#")
    if arg:
        if not arg.isdigit() or int(arg) not in opts:
            raise GateError(f"`/approve {arg}`: pick one of {sorted(opts)}.")
        return opts[int(arg)]
    ticked = [int(n) for n in TICKED.findall(issue_body or "")]
    if len(ticked) != 1:
        raise GateError(f"Tick exactly one box (found {len(ticked)}) or write `/approve <number>`.")
    if ticked[0] not in opts:
        raise GateError("The ticked line is not a topic option.")
    return opts[ticked[0]]


def _write_output(**kv: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    lines = []
    for k, v in kv.items():
        v = v.replace("\n", " ")
        lines.append(f"{k}={v}")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def main(argv=None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if argv[:1] != ["resolve-topic"]:
        raise SystemExit("usage: python -m pipeline.gate resolve-topic")
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
        event = json.load(f)
    try:
        title, wiki = resolve_topic(event["comment"]["body"], event["issue"]["body"])
    except GateError as e:
        _write_output(error=str(e))
        sys.exit(1)
    _write_output(title=title, wiki=wiki)


if __name__ == "__main__":
    main()
