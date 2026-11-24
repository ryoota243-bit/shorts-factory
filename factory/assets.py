"""Find a photo/video for a scene: local file -> Pexels -> Pixabay -> AI generation.

Keys come from environment variables: PEXELS_API_KEY, PIXABAY_API_KEY, GEMINI_API_KEY.
A source without a key is skipped silently.
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


def _pexels(v, want_video, cache):
    key = os.environ.get("PEXELS_API_KEY")
    if not key or not v.get("query"):
        return None
    q = urllib.parse.quote(v["query"])
    h = {"Authorization": key}
    if want_video:
        res = _get_json(f"https://api.pexels.com/videos/search?query={q}&orientation=portrait&per_page=5", h)
        for vid in res.get("videos", []):
            files = sorted((f for f in vid["video_files"] if f.get("height", 0) >= 1280),
                           key=lambda f: f["height"])
            if files:
                return {"kind": "video", "path": _download(files[0]["link"], cache, ".mp4"),
                        "credit": f"Pexels: {vid['user']['name']} {vid['url']}"}
    res = _get_json(f"https://api.pexels.com/v1/search?query={q}&per_page=5", h)
    for p in res.get("photos", []):
        return {"kind": "image", "path": _download(p["src"]["large2x"], cache, ".jpg"),
                "credit": f"Pexels: {p['photographer']} {p['url']}"}
    return None


def _pixabay(v, want_video, cache):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key or not v.get("query"):
        return None
    q = urllib.parse.quote(v["query"])
    if want_video:
        res = _get_json(f"https://pixabay.com/api/videos/?key={key}&q={q}&per_page=5")
        for hit in res.get("hits", []):
            f = hit["videos"].get("large") or hit["videos"].get("medium")
            if f and f.get("url"):
                return {"kind": "video", "path": _download(f["url"], cache, ".mp4"),
                        "credit": f"Pixabay: {hit['user']} {hit['pageURL']}"}
    res = _get_json(f"https://pixabay.com/api/?key={key}&q={q}&image_type=photo&per_page=5")
    for hit in res.get("hits", []):
        return {"kind": "image", "path": _download(hit["largeImageURL"], cache, ".jpg"),
                "credit": f"Pixabay: {hit['user']} {hit['pageURL']}"}
    return None


def _ai(v, cfg, cache):
    key = os.environ.get("GEMINI_API_KEY")
    prompt = v.get("ai_prompt")
    if not key or not prompt:
        return None
    path = Path(cache) / ("ai_" + hashlib.sha1(prompt.encode()).hexdigest()[:16] + ".png")
    if not path.exists():
        model = cfg["assets"].get("ai_model", "gemini-2.5-flash-image")
        body = {"contents": [{"parts": [{"text": prompt + " Vertical 9:16 composition, no text."}]}],
                "generationConfig": {"responseModalities": ["IMAGE"],
                                     "imageConfig": {"aspectRatio": "9:16"}}}
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "x-goog-api-key": key})
        res = json.loads(urllib.request.urlopen(req, timeout=180).read())
        parts = res["candidates"][0]["content"]["parts"]
        data = next(p["inlineData"]["data"] for p in parts if "inlineData" in p)
        path.write_bytes(base64.b64decode(data))
    return {"kind": "image", "path": path, "credit": "AI generated"}


def resolve(v, cfg, cache, root):
    """Return {"kind": "image"|"video", "path", "credit"} or None."""
    if v.get("type") not in ("photo", "video", "ai"):
        return None
    want_video = v["type"] == "video" or cfg["assets"].get("prefer_video", False)
    order = v.get("sources") or (["ai"] if v["type"] == "ai" else cfg["assets"]["order"])
    for src in order:
        try:
            if src == "local" and v.get("file"):
                p = Path(root) / v["file"]
                if p.exists():
                    kind = "video" if p.suffix.lower() in (".mp4", ".mov", ".webm") else "image"
                    return {"kind": kind, "path": p, "credit": "local"}
            elif src == "pexels":
                r = _pexels(v, want_video, cache)
                if r:
                    return r
            elif src == "pixabay":
                r = _pixabay(v, want_video, cache)
                if r:
                    return r
            elif src == "ai":
                r = _ai(v, cfg, cache)
                if r:
                    return r
        except Exception as e:  # network/API errors -> try next source
            print(f"  [assets] {src} failed for {v.get('query') or v.get('ai_prompt')}: {e}")
    return None
