"""Small shared helpers: slugs, HTTP session, text normalisation."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

import requests

USER_AGENT = "TickAndTomorrowPipeline/0.1 (educational science explainer research; python-requests)"


def http() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def slugify(text: str, max_len: int = 50) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return text[:max_len].rstrip("-") or "episode"


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


_QUOTES = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-",
    " ": " ", " ": " ", " ": " ",
})


def normalize(text: str) -> str:
    """Lower-case, unify quotes/dashes/spaces so quote matching ignores typography."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES).lower()
    text = re.sub(r"\[\d+\]", "", text)          # footnote markers like [12]
    return re.sub(r"\s+", " ", text).strip()
