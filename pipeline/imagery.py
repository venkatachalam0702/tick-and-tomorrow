"""Photos (Wikimedia Commons, Openverse), FLUX illustrations (Cloudflare) and text cards.

Every image ends up as a 1920x1080 PNG. Only licenses on the allow-list are used, and the
author, license and source link of every photo go to credits.json for the description.
"""
from __future__ import annotations

import base64
import io
import os
import re
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .brand import COLORS, H, W, font_path
from .util import http

COMMONS = "https://commons.wikimedia.org/w/api.php"
OPENVERSE = "https://api.openverse.org/v1/images/"
FLUX = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/@cf/black-forest-labs/flux-1-schnell"

STYLE = ("Clean modern editorial illustration, dark navy background, amber and teal accent light, "
         "soft cinematic lighting, simple shapes, high detail, 16:9 composition. "
         "No text, no letters, no numbers, no logos, no watermark. Scene: ")

_OK_LICENSE = re.compile(r"^(public domain|pd\b|pdm|cc0|cc[- ]?by(?:[- ]?sa)?(?:[- ]?\d(\.\d)?)?$|cc[- ]?by(?:[- ]?sa)?[- ]\d)",
                         re.I)
_BAD_LICENSE = re.compile(r"\bnc\b|\bnd\b|non[- ]?commercial|no[- ]?deriv|fair use|all rights", re.I)


def license_ok(name: str) -> bool:
    name = (name or "").strip()
    return bool(name) and not _BAD_LICENSE.search(name) and bool(_OK_LICENSE.search(name))


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


# ------------------------------------------------------------------ framing

def fit(img: Image.Image, mode: str = "contain") -> Image.Image:
    """Return a 1920x1080 frame: 'cover' crops to fill, 'contain' shows the whole image on a blurred backdrop."""
    img = img.convert("RGB")
    cover = img.copy()
    scale = max(W / cover.width, H / cover.height)
    cover = cover.resize((max(W, round(cover.width * scale)), max(H, round(cover.height * scale))), Image.LANCZOS)
    left, top = (cover.width - W) // 2, (cover.height - H) // 2
    cover = cover.crop((left, top, left + W, top + H))
    if mode == "cover":
        return cover
    bg = cover.filter(ImageFilter.GaussianBlur(40))
    bg = Image.blend(bg, Image.new("RGB", (W, H), COLORS["bg2"]), 0.55)
    scale = min(W / img.width, H / img.height)
    fg = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    return bg


# ------------------------------------------------------------------ text cards

def text_card(headline: str, sub: str, out: Path) -> Path:
    img = Image.new("RGB", (W, H), COLORS["bg2"])
    d = ImageDraw.Draw(img)
    for y in range(H):  # vertical gradient
        t = y / H
        c1, c2 = Image.new("RGB", (1, 1), COLORS["bg1"]).getpixel((0, 0)), Image.new("RGB", (1, 1), COLORS["bg2"]).getpixel((0, 0))
        d.line([(0, y), (W, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(c1, c2)))
    big = ImageFont.truetype(font_path(True), 110)
    small = ImageFont.truetype(font_path(False), 54)
    lines = textwrap.wrap(headline.strip(), 26)[:4]
    sub_lines = textwrap.wrap((sub or "").strip(), 50)[:2]
    total = len(lines) * 130 + 70 + len(sub_lines) * 70
    y = (H - total) // 2
    for ln in lines:
        w = d.textlength(ln, font=big)
        d.text(((W - w) / 2, y), ln, font=big, fill=COLORS["ink"])
        y += 130
    d.rounded_rectangle([(W / 2 - 90, y + 25), (W / 2 + 90, y + 37)], radius=6, fill=COLORS["warm"])
    y += 70
    for ln in sub_lines:
        w = d.textlength(ln, font=small)
        d.text(((W - w) / 2, y), ln, font=small, fill=COLORS["cool"])
        y += 70
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


# ------------------------------------------------------------------ photos

def commons_candidates(query: str, limit: int = 8) -> list[dict]:
    params = {"action": "query", "format": "json", "formatversion": 2, "generator": "search",
              "gsrnamespace": 6, "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": limit,
              "prop": "imageinfo", "iiprop": "url|extmetadata|size|mime", "iiurlwidth": 1920}
    r = http().get(COMMONS, params=params, timeout=30)
    r.raise_for_status()
    out = []
    pages = sorted(r.json().get("query", {}).get("pages", []), key=lambda p: p.get("index", 0))
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        lic = _strip_html(meta.get("LicenseShortName", {}).get("value", ""))
        if info.get("width", 0) < 800 or not license_ok(lic) or info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        out.append({"url": info.get("thumburl") or info.get("url"), "page": info.get("descriptionurl", ""),
                    "title": p.get("title", "").removeprefix("File:"), "license": lic,
                    "author": _strip_html(meta.get("Artist", {}).get("value", ""))[:120] or "Unknown",
                    "provider": "Wikimedia Commons"})
    return out


def openverse_candidates(query: str, limit: int = 8) -> list[dict]:
    r = http().get(OPENVERSE, params={"q": query, "license": "cc0,pdm,by,by-sa", "page_size": limit,
                                       "mature": "false"}, timeout=30)
    if r.status_code != 200:
        return []
    out = []
    for it in r.json().get("results", []):
        lic = f"{it.get('license', '')} {it.get('license_version') or ''}".strip()
        lic = {"cc0": "CC0", "pdm": "Public domain"}.get(it.get("license"), f"CC {lic.upper()}")
        if (it.get("width") or 0) < 800 or not license_ok(lic):
            continue
        out.append({"url": it.get("url"), "page": it.get("foreign_landing_url", ""), "title": it.get("title", ""),
                    "license": lic, "author": it.get("creator") or "Unknown",
                    "provider": f"Openverse ({it.get('source', '')})"})
    return out


def photo_candidates(query: str) -> list[dict]:
    found = []
    for fn in (commons_candidates, openverse_candidates):
        try:
            found += fn(query)
        except Exception as e:  # noqa: BLE001 - a provider being down should not stop the run
            print(f"[warn] {fn.__name__} failed for {query!r}: {e}")
    return found


def download_photo(c: dict, out: Path) -> Path:
    r = http().get(c["url"], timeout=60)
    r.raise_for_status()
    fit(Image.open(io.BytesIO(r.content)), "contain").save(out)
    return out


# ------------------------------------------------------------------ illustrations

def flux_illustration(prompt: str, out: Path, seed: int = 0) -> Path:
    account, token = os.environ.get("CF_ACCOUNT_ID", "").strip(), os.environ.get("CF_API_TOKEN", "").strip()
    if not account or not token:
        raise RuntimeError("CF_ACCOUNT_ID / CF_API_TOKEN are not set (repo Settings > Secrets > Actions).")
    r = http().post(FLUX.format(account=account), headers={"Authorization": f"Bearer {token}"},
                    json={"prompt": (STYLE + prompt)[:2048], "steps": 6, "seed": seed}, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Cloudflare FLUX HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    img_b64 = (data.get("result") or {}).get("image")
    if not img_b64:
        raise RuntimeError(f"Cloudflare FLUX returned no image: {str(data)[:300]}")
    fit(Image.open(io.BytesIO(base64.b64decode(img_b64))), "cover").save(out)
    return out
