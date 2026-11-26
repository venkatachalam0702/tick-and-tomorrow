"""Fact sheet: quote verification in code, then facts.md for Gate 2.

facts.md is the human-editable source of truth. Each claim is one block:

    ### C07
    **Claim:** Microwave ovens usually run at 2.45 GHz.
    > "most commonly 2.45 GHz" ([S1](https://...))

Venkat deletes a block to drop a claim, or edits the **Claim:** line to correct it.
The script stage reads it back with parse_facts_md().
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .util import normalize

NUMBER = re.compile(r"\d")


@dataclass
class Source:
    id: str
    title: str
    url: str
    text: str
    kind: str = "wikipedia"
    fetched: str = ""


@dataclass
class Claim:
    id: str
    text: str
    section: str = ""
    evidence: list[dict] = field(default_factory=list)   # verified {source_id, quote}
    rejected: list[dict] = field(default_factory=list)   # quotes not found in the source

    @property
    def has_number(self) -> bool:
        return bool(NUMBER.search(self.text))

    @property
    def source_ids(self) -> list[str]:
        return sorted({e["source_id"] for e in self.evidence})

    @property
    def status(self) -> str:
        if not self.evidence:
            return "dropped"
        if self.has_number and len(self.source_ids) < 2:
            return "single-source"
        return "verified"


def quote_in_text(quote: str, text_norm: str) -> bool:
    """True if the quote appears verbatim (after normalisation) in the source.

    "..." or "…" in a quote splits it into pieces that must appear in order.
    Very short quotes are rejected: they prove nothing.
    """
    q = normalize(quote).strip(" \"'")
    pieces = [p.strip(" \"'.,;:") for p in re.split(r"\.\.\.|…", q)]
    pieces = [p for p in pieces if p]
    if not pieces or sum(len(p) for p in pieces) < 15:
        return False
    pos = 0
    for p in pieces:
        i = text_norm.find(p, pos)
        if i < 0:
            return False
        pos = i + len(p)
    return True


def verify(raw_claims: list[dict], sources: dict[str, Source]) -> list[Claim]:
    norm = {sid: normalize(s.text) for sid, s in sources.items()}
    out: list[Claim] = []
    for n, rc in enumerate(raw_claims, 1):
        c = Claim(id=f"C{n:02d}", text=rc.get("claim", "").strip(), section=rc.get("section", "").strip())
        seen = set()
        for ev in rc.get("evidence", []):
            sid, quote = ev.get("source_id", "").strip(), ev.get("quote", "").strip()
            key = (sid, normalize(quote))
            if key in seen:
                continue
            seen.add(key)
            if sid in norm and quote_in_text(quote, norm[sid]):
                c.evidence.append({"source_id": sid, "quote": quote})
            else:
                c.rejected.append({"source_id": sid, "quote": quote})
        if c.text:
            out.append(c)
    return out


def _quote_md(q: str) -> str:
    return q.replace("\n", " ").replace("|", "\\|")


def render_facts_md(title: str, claims: list[Claim], sources: dict[str, Source], usage: str) -> str:
    kept = [c for c in claims if c.status != "dropped"]
    dropped = [c for c in claims if c.status == "dropped"]
    single = [c for c in kept if c.status == "single-source"]
    lines = [
        f"# Fact sheet: {title}",
        "",
        f"**{len(kept)} claims kept** ({len(kept) - len(single)} verified, {len(single)} ⚠️ single-source numbers). "
        f"{len(dropped)} dropped because their quote was not found in the saved source.",
        "",
        "Every quote below was checked by code against the saved source text in `sources/`.",
        "",
        "## How to review (Gate 2)",
        "1. Read each claim. Open the link if a claim looks doubtful.",
        "2. **Wrong or unneeded?** Delete its whole `### Cxx` block.",
        "3. **Slightly off?** Edit the `**Claim:**` line only (keep the quote as it is).",
        "4. ⚠️ **single-source** numbers have only one source: keep them only if you trust it.",
        "5. Merge the pull request to approve. The script will use only the claims left here.",
        "",
        "## Claims",
        "",
    ]
    section = None
    for c in kept:
        if c.section and c.section != section:
            section = c.section
            lines += [f"#### {section}", ""]
        flag = " ⚠️ single-source" if c.status == "single-source" else ""
        lines.append(f"### {c.id}{flag}")
        lines.append(f"**Claim:** {c.text}")
        for e in c.evidence:
            s = sources[e["source_id"]]
            lines.append(f"> \"{_quote_md(e['quote'])}\" ([{s.id}: {s.title}]({s.url}))")
        lines.append("")
    lines += ["## Sources", ""]
    for s in sources.values():
        lines.append(f"- **{s.id}** [{s.title}]({s.url}) · {s.kind} · fetched {s.fetched} · `sources/{s.id}.md`")
    if dropped:
        lines += ["", "<details><summary>Dropped claims (quote not found in source)</summary>", ""]
        for c in dropped:
            lines.append(f"- {c.text}")
        lines += ["", "</details>"]
    lines += ["", f"<sub>Gemini usage for this run: {usage}</sub>", ""]
    return "\n".join(lines)


_BLOCK = re.compile(r"^### (C\d+)[^\n]*\n\*\*Claim:\*\*\s*(.+?)\s*$", re.M)


def parse_facts_md(md: str) -> list[dict]:
    """Return the claims still present in a (possibly human-edited) facts.md."""
    body = md.split("\n## Sources", 1)[0]
    return [{"id": cid, "claim": text} for cid, text in _BLOCK.findall(body)]
