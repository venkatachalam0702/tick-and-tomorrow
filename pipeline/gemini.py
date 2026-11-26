"""Minimal Gemini API client (REST, no SDK) with retries and a per-run call log.

Free-tier note: prompts sent on the free tier may be used by Google to improve its
models, so only public material (fetched web sources, our own drafts) is ever sent.
"""
from __future__ import annotations

import base64
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


def models_for(role: str) -> list[str]:
    cfg = config.load()["gemini"]
    return [cfg[f"{role}_model"], *cfg.get(f"{role}_fallbacks", [])]


def generate(prompt: str, *, role: str = "helper", json_schema: dict | bool | None = None,
             temperature: float | None = None, purpose: str = "",
             images: list[tuple[bytes, str]] | None = None) -> str | dict:
    """Send one prompt. Returns parsed JSON when json_schema is given, else text.

    Overload (503) and rate limits (429) are retried with growing waits; if the main model
    stays unavailable, the role's fallback models are tried in order.
    """
    cfg = config.load()["gemini"]
    gen_cfg: dict = {"temperature": cfg["temperature"] if temperature is None else temperature}
    if json_schema is not None:  # a schema dict, or True for "any JSON"
        gen_cfg["responseMimeType"] = "application/json"
        if isinstance(json_schema, dict) and json_schema:
            gen_cfg["responseSchema"] = json_schema
    parts: list[dict] = [{"text": prompt}]
    for data, mime in images or []:  # vision: (bytes, "image/png")
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
    body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen_cfg}

    errors = []
    for model in models_for(role):
        err = _try_model(model, body, role, purpose, json_schema is not None, cfg)
        if not isinstance(err, str):
            return err[0]
        errors.append(f"{model}: {err}")
        print(f"[warn] {model} unavailable for {purpose!r}; trying next model if any.")
    raise GeminiError(f"Gemini call failed ({purpose}). " + " | ".join(errors))


def _try_model(model, body, role, purpose, want_json, cfg):
    """Return (result,) on success, or an error string to move on to the next model."""
    last_err = ""
    for attempt in range(1, cfg["max_retries"] + 1):
        CALL_LOG.append({"model": model, "role": role, "purpose": purpose, "attempt": attempt})
        try:
            r = requests.post(API.format(model=model), json=body, timeout=300,
                              headers={"x-goog-api-key": _key()})
        except requests.RequestException as e:  # network blip or timeout
            last_err = str(e)[:300]
        else:
            if r.status_code == 200:
                return (_parse(r.json(), want_json),)
            # Never echo headers (they carry the key); the body is safe to show.
            last_err = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code == 429 and "per day" in r.text.lower():
                return f"daily free-tier quota used up ({last_err})"
            if r.status_code not in (429, 500, 502, 503, 504):
                raise GeminiError(f"Gemini rejected the request ({model}, {purpose}): {last_err}")
        if attempt < cfg["max_retries"]:
            wait = cfg["retry_wait_seconds"] * attempt
            print(f"[warn] {model} attempt {attempt} failed ({last_err[:80]}...); waiting {wait}s")
            time.sleep(wait)
    return last_err


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
    """Requests per model (role 'writer' is Flash and scarce; 'helper' is Flash-Lite)."""
    if not CALL_LOG:
        return "No Gemini calls."
    by_model: dict[str, int] = {}
    for c in CALL_LOG:
        by_model[c["model"]] = by_model.get(c["model"], 0) + 1
    return ", ".join(f"{m}: {n} request(s)" for m, n in by_model.items())
