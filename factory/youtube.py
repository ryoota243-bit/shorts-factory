"""Upload a finished video to YouTube (YouTube Data API v3, OAuth refresh token).

Secrets (environment): YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN
(another channel: the names in its config, youtube.secret_env).
Until Google audits the API project, uploads are forced to "private" by YouTube itself.
"""
import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMB_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"


# Environment variable names of the OAuth secrets. A second channel sets its own names in its config
# (youtube.secret_env). The refresh token picks the channel, so it never falls back to the main one;
# the OAuth client (id / secret) may be shared and falls back to the default names.
ENV = {"client_id": "YOUTUBE_CLIENT_ID", "client_secret": "YOUTUBE_CLIENT_SECRET",
       "refresh_token": "YOUTUBE_REFRESH_TOKEN"}
DEFAULT_ENV = dict(ENV)


def configure(cfg):
    ENV.update(DEFAULT_ENV)
    ENV.update((cfg.get("youtube") or {}).get("secret_env") or {})


def _secret(key):
    v = os.environ.get(ENV[key])
    if not v and key != "refresh_token":
        v = os.environ.get(DEFAULT_ENV[key])
    return v


def configured():
    return all(_secret(k) for k in ENV)


def _open(req, timeout=600):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            msg = json.loads(body)["error"]
            msg = msg.get("message") or msg.get("error_description") or str(msg) if isinstance(msg, dict) else msg
        except Exception:
            msg = body[:500]
        raise RuntimeError(f"YouTube API {e.code}: {msg}") from None


def access_token():
    data = urllib.parse.urlencode({
        "client_id": _secret("client_id"),
        "client_secret": _secret("client_secret"),
        "refresh_token": _secret("refresh_token"),
        "grant_type": "refresh_token",
    }).encode()
    res = json.loads(_open(urllib.request.Request(TOKEN_URL, data=data), 60).read())
    return res["access_token"]


def _clean(text, limit):
    text = re.sub(r"[<>]", "", text or "").replace("[", "").replace("]", "")
    return text[:limit]


def _tags(tags):
    out, total = [], 0
    for t in tags or []:
        t = _clean(t, 60).lstrip("#").strip()
        if t and total + len(t) + 2 <= 450:
            out.append(t)
            total += len(t) + 2
    return out


def build_body(meta, cfg):
    yc = cfg.get("youtube", {})
    status = {
        "privacyStatus": yc.get("privacy", "private"),
        "selfDeclaredMadeForKids": False,
        "embeddable": True,
    }
    if yc.get("declare_synthetic", True):
        status["containsSyntheticMedia"] = any("AI" in c for c in meta.get("credits", []))
    if yc.get("publish_at") and status["privacyStatus"] == "private":
        status["publishAt"] = yc["publish_at"]
    return {
        "snippet": {
            "title": _clean(meta.get("title"), 100),
            "description": _clean(meta.get("description"), 4900),
            "tags": _tags(meta.get("tags")),
            "categoryId": str(yc.get("category_id", "27")),  # 27 = Education
            "defaultLanguage": "ja",
            "defaultAudioLanguage": "ja",
        },
        "status": status,
    }


def _insert(token, body, mp4):
    size = Path(mp4).stat().st_size
    init = urllib.request.Request(
        f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    location = _open(init, 60).headers["Location"]
    put = urllib.request.Request(location, data=Path(mp4).read_bytes(), method="PUT",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4",
                                          "Content-Length": str(size)})
    return json.loads(_open(put).read())


def _thumbnail(token, video_id, png):
    from PIL import Image
    buf = io.BytesIO()
    img = Image.open(png).convert("RGB")
    q = 90
    while True:  # YouTube limit: 2 MB
        buf.seek(0); buf.truncate()
        img.save(buf, "JPEG", quality=q)
        if buf.tell() < 1_900_000 or q <= 50:
            break
        q -= 10
    req = urllib.request.Request(f"{THUMB_URL}?videoId={video_id}", data=buf.getvalue(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"})
    _open(req, 120).read()


def upload(mp4, meta, cfg, thumb_png=None):
    """Upload one video. Returns {"id", "url", "privacy", "thumbnail": bool, "note"}."""
    token = access_token()
    body = build_body(meta, cfg)
    try:
        res = _insert(token, body, mp4)
    except RuntimeError as e:
        if "containsSyntheticMedia" in str(e) or "400" in str(e) and "status" in str(e):
            body["status"].pop("containsSyntheticMedia", None)  # older API surface: retry without it
            res = _insert(token, body, mp4)
        else:
            raise
    vid = res["id"]
    out = {"id": vid, "url": f"https://youtube.com/shorts/{vid}",
           "privacy": res.get("status", {}).get("privacyStatus", body["status"]["privacyStatus"]),
           "thumbnail": False, "note": ""}
    if thumb_png and Path(thumb_png).exists():
        try:
            _thumbnail(token, vid, thumb_png)
            out["thumbnail"] = True
        except Exception as e:  # needs a phone-verified channel; Shorts may ignore it
            out["note"] = f"サムネイル未設定: {e}"
    return out
