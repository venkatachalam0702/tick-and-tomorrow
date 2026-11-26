"""Wikipedia / Wikimedia helpers (free APIs, no key)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .util import http

API = "https://en.wikipedia.org/w/api.php"
PAGEVIEWS = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
             "en.wikipedia/all-access/user/{title}/daily/{start}/{end}")

# Sections that hold no explainable facts.
_DROP_SECTIONS = re.compile(
    r"\n=+\s*(See also|References|Notes|Further reading|External links|Bibliography|Sources|Citations|Footnotes)\s*=+.*",
    re.S | re.I,
)


def _get(params: dict) -> dict:
    params = {"format": "json", "formatversion": 2, **params}
    r = http().get(API, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def resolve_titles(titles: list[str]) -> list[str]:
    """Return canonical titles (redirects followed) for the titles that exist, in order."""
    if not titles:
        return []
    data = _get({"action": "query", "titles": "|".join(titles[:20]), "redirects": 1})
    q = data.get("query", {})
    mapping = {t: t for t in titles}
    for key in ("normalized", "redirects"):
        for item in q.get(key, []):
            for orig, cur in list(mapping.items()):
                if cur == item["from"]:
                    mapping[orig] = item["to"]
    existing = {p["title"] for p in q.get("pages", []) if not p.get("missing") and not p.get("invalid")}
    out: list[str] = []
    for t in titles:
        c = mapping.get(t)
        if c in existing and c not in out:
            out.append(c)
    return out


def search(query: str, limit: int = 3) -> list[str]:
    data = _get({"action": "query", "list": "search", "srsearch": query, "srlimit": limit})
    return [h["title"] for h in data.get("query", {}).get("search", [])]


def article(title: str) -> dict:
    """Plain-text article body plus its canonical URL and external reference links."""
    data = _get({"action": "query", "titles": title, "redirects": 1,
                 "prop": "extracts|info|extlinks", "explaintext": 1, "inprop": "url",
                 "ellimit": "max"})
    page = data["query"]["pages"][0]
    text = _DROP_SECTIONS.sub("", page.get("extract", "")).strip()
    links = [e.get("url", "") for e in page.get("extlinks", [])]
    return {"title": page["title"], "url": page.get("fullurl", ""), "text": text,
            "extlinks": [u for u in links if u.startswith("http")]}


def pageviews(title: str, days: int = 30) -> int:
    """Total human pageviews over the last `days` days (0 if unknown)."""
    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    url = PAGEVIEWS.format(title=quote(title.replace(" ", "_"), safe=""),
                           start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
    try:
        r = http().get(url, timeout=30)
        if r.status_code != 200:
            return 0
        return sum(i.get("views", 0) for i in r.json().get("items", []))
    except Exception:
        return 0
