"""Minimal Gemini API client (REST, no SDK) with retries and a per-run call log.

Free-tier note: prompts sent on the free tier may be used by Google to improve its
models, so only public material (fetched web sources, our own drafts) is ever sent.
"""
from __future__ import annotations

import json
import os
import time

import requests

from . import config

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Every call made in this process, so stages can report usage against the daily cap.
CALL_LOG: list[dict] = []


class GeminiError(RuntimeError):
    pass


def _key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise GeminiError("GEMINI_API_KEY is not set (add it under repo Settings > Secrets > Actions).")
    return key


def model_name(role: str) -> str:
    """role is 'writer' (Flash, scarce) or 'helper' (Flash-Lite, plentiful)."""
    return config.load()["gemini"][f"{role}_model"]


def generate(prompt: str, *, role: str = "helper", json_schema: dict | None = None,
             temperature: float | None = None, purpose: str = "") -> str | dict:
    """Send one prompt. Returns parsed JSON when json_schema is given, else text."""
    cfg = config.load()["gemini"]
    model = model_name(role)
    gen_cfg: dict = {"temperature": cfg["temperature"] if temperature is None else temperature}
    if json_schema is not None:
        gen_cfg["responseMimeType"] = "application/json"
        gen_cfg["responseSchema"] = json_schema
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen_cfg}

    last_err = ""
    for attempt in range(1, cfg["max_retries"] + 1):
        CALL_LOG.append({"model": model, "role": role, "purpose": purpose, "attempt": attempt})
        try:
            r = requests.post(API.format(model=model), json=body, timeout=300,
                              headers={"x-goog-api-key": _key()})
        except requests.RequestException as e:  # network blip
            last_err = str(e)
        else:
            if r.status_code == 200:
                return _parse(r.json(), json_schema is not None)
            # Never echo headers (they carry the key); the body is safe to show.
            last_err = f"HTTP {r.status_code}: {r.text[:500]}"
            if r.status_code not in (429, 500, 502, 503, 504):
                break
            if r.status_code == 429 and "per day" in r.text.lower():
                raise GeminiError(f"Daily free-tier quota for {model} is used up. Try again after midnight Pacific. {last_err}")
        time.sleep(cfg["retry_wait_seconds"] * attempt)
    raise GeminiError(f"Gemini call failed ({model}, {purpose}): {last_err}")


def _parse(data: dict, want_json: bool) -> str | dict:
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError) as e:
        raise GeminiError(f"Unexpected Gemini response: {json.dumps(data)[:500]}") from e
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not want_json:
        return text
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return json.loads(text)


def usage_summary() -> str:
    if not CALL_LOG:
        return "No Gemini calls."
    by_model: dict[str, int] = {}
    for c in CALL_LOG:
        by_model[c["model"]] = by_model.get(c["model"], 0) + 1
    return ", ".join(f"{m}: {n} request(s)" for m, n in by_model.items())
