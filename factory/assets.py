"""Find a photo/video for a scene: local file -> Pexels -> Pixabay -> AI generation.

Keys come from environment variables: PEXELS_API_KEY, PIXABAY_API_KEY, GEMINI_API_KEY.
A source without a key is skipped silently. type "ai" tries AI first, then stock photos.
"""
import base64
import hashlib
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

UA = {"User-Agent": "shorts-factory/1.0"}


def _get_json(url, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def _download(url, cache, ext):
    path = Path(cache) / (hashlib.sha1(url.encode()).hexdigest()[:16] + ext)
    if not path.exists():
        req = urllib.request.Request(url, headers=UA)
        path.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    return path


def _pexels(v, want_video, cache, n=3):
    key = os.environ.get("PEXELS_API_KEY")
    if not key or not v.get("query"):
        return
    q = urllib.parse.quote(v["query"])
    h = {"Authorization": key}
    if want_video:
        res = _get_json(f"https://api.pexels.com/videos/search?query={q}&orientation=portrait&per_page=5", h)
        for vid in res.get("videos", []):
            files = sorted((f for f in vid["video_files"] if f.get("height", 0) >= 1280),
                           key=lambda f: f["height"])
            if files:
                yield {"kind": "video", "path": _download(files[0]["link"], cache, ".mp4"),
                       "credit": f"Pexels: {vid['user']['name']} {vid['url']}"}
    res = _get_json(f"https://api.pexels.com/v1/search?query={q}&per_page=5", h)
    for p in res.get("photos", [])[:n]:
        yield {"kind": "image", "path": _download(p["src"]["large2x"], cache, ".jpg"),
               "credit": f"Pexels: {p['photographer']} {p['url']}"}


def _pixabay(v, want_video, cache, n=3):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key or not v.get("query"):
        return
    q = urllib.parse.quote(v["query"])
    if want_video:
        res = _get_json(f"https://pixabay.com/api/videos/?key={key}&q={q}&per_page=5")
        for hit in res.get("hits", []):
            f = hit["videos"].get("large") or hit["videos"].get("medium")
            if f and f.get("url"):
                yield {"kind": "video", "path": _download(f["url"], cache, ".mp4"),
                       "credit": f"Pixabay: {hit['user']} {hit['pageURL']}"}
    res = _get_json(f"https://pixabay.com/api/?key={key}&q={q}&image_type=photo&per_page=5")
    for hit in res.get("hits", [])[:n]:
        yield {"kind": "image", "path": _download(hit["largeImageURL"], cache, ".jpg"),
               "credit": f"Pixabay: {hit['user']} {hit['pageURL']}"}


def _ai(v, cfg, cache, avoid=""):
    prompt = v.get("ai_prompt")
    if not os.environ.get("GEMINI_API_KEY") or not prompt:
        return None
    if avoid:
        prompt += f". Important: {avoid}"
    extra = (cfg.get("assets") or {}).get("ai_suffix", "")  # a channel's standing image rules
    if extra:
        prompt += f". {extra}"
    path = Path(cache) / ("ai_" + hashlib.sha1(prompt.encode()).hexdigest()[:16] + ".png")
    if not path.exists():
        from . import gemini
        full = (prompt + ". Vertical 9:16 composition. No text, letters, logos, emblems, patches, flags-as-logos"
                " or watermarks in the image."
                " Do not depict the face of any real, identifiable person.")
        path.write_bytes(gemini.generate_image(full))
    return {"kind": "image", "path": path, "credit": "AI生成画像 (Gemini)"}


def _candidates(src, v, cfg, cache, root, want_video):
    if src == "local" and v.get("file"):
        p = Path(root) / v["file"]
        if p.exists():
            kind = "video" if p.suffix.lower() in (".mp4", ".mov", ".webm") else "image"
            yield {"kind": kind, "path": p, "credit": "local", "trusted": True}
    elif src == "pexels":
        yield from _pexels(v, want_video, cache)
    elif src == "pixabay":
        yield from _pixabay(v, want_video, cache)
    elif src == "ai":
        r = _ai(v, cfg, cache)
        if r:
            yield r


def resolve(v, cfg, cache, root, narration="", topic=""):
    """Return {"kind": "image"|"video", "path", "credit"} or None.

    Every stock/AI candidate is checked by Claude against the narration; a mismatch moves on
    to the next candidate, and a rejected AI image is regenerated once with the reason."""
    from .verify import check
    if v.get("type") not in ("photo", "video", "ai"):
        return None
    want_video = v["type"] == "video" or cfg["assets"].get("prefer_video", False)
    order = v.get("sources") or (["local", "ai", "pexels", "pixabay"] if v["type"] == "ai"
                                 else cfg["assets"]["order"])
    want = v.get("ai_prompt") or v.get("query") or ""
    for src in order:
        try:
            for cand in _candidates(src, v, cfg, cache, root, want_video):
                if cand.get("trusted"):
                    return cand
                ok, why = check(cand["path"], narration, want, topic)
                if ok:
                    return cand
                print(f"  [assets] rejected {src} image: {why}")
                if src == "ai":
                    retry = _ai(v, cfg, cache, avoid=f"The previous image was wrong: {why}. "
                                                    "Depict the subject accurately and generically.")
                    if retry and check(retry["path"], narration, want, topic)[0]:
                        return retry
                    print("  [assets] AI retry also rejected")
        except Exception as e:  # network/API errors -> try next source
            print(f"  [assets] {src} failed for {v.get('query') or v.get('ai_prompt')}: {e}")
    return None
