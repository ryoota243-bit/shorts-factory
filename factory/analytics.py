"""YouTube Analytics: per-video performance, grouped by format and category, and gentle weight tuning.

Needs the refresh token to include https://www.googleapis.com/auth/yt-analytics.readonly.
Analytics data lags about 2 days, so very new videos show little or nothing.
"""
import datetime as dt
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from . import youtube

API = "https://youtubeanalytics.googleapis.com/v2/reports"
METRICS = ["views", "averageViewPercentage", "averageViewDuration", "likes", "comments", "shares",
           "subscribersGained"]
MIN_VIDEOS = 3        # a group needs this many measured videos before it changes any weight
MIN_VIEWS = 50        # below this a video is unpublished/too new to say anything about the format
WEIGHT_RANGE = (0.5, 2.0)


def _video_id(url):
    m = re.search(r"(?:shorts/|v=|youtu\.be/)([\w-]{11})", url or "")
    return m.group(1) if m else None


def fetch(start="2026-11-01"):
    token = youtube.access_token()
    q = urllib.parse.urlencode({
        "ids": "channel==MINE", "startDate": start, "endDate": dt.date.today().isoformat(),
        "metrics": ",".join(METRICS), "dimensions": "video", "sort": "-views", "maxResults": 200,
    })
    req = urllib.request.Request(f"{API}?{q}", headers={"Authorization": f"Bearer {token}"})
    res = json.loads(youtube._open(req, 60).read())
    cols = [h["name"] for h in res.get("columnHeaders", [])]
    return {r[0]: dict(zip(cols[1:], r[1:])) for r in res.get("rows", [])}


def _titles(ids):
    """Titles for videos we did not upload ourselves (YouTube Data API, 1 unit per 50 ids)."""
    out = {}
    ids = list(ids)
    if not ids:
        return out
    token = youtube.access_token()
    for i in range(0, len(ids), 50):
        q = urllib.parse.urlencode({"part": "snippet", "id": ",".join(ids[i:i + 50])})
        req = urllib.request.Request(f"https://www.googleapis.com/youtube/v3/videos?{q}",
                                     headers={"Authorization": f"Bearer {token}"})
        for it in json.loads(youtube._open(req, 60).read()).get("items", []):
            out[it["id"]] = it["snippet"]["title"]
    return out


def build(root, history):
    """Join analytics rows with our history/scripts and write analytics/report.{json,md}."""
    rows = fetch()
    archive = Path(root) / "scripts/archive"
    videos = []
    for h in history:
        for url in h.get("youtube", []) or []:
            vid = _video_id(url)
            if not vid:
                continue
            script = {}
            for cand in archive.glob(f"{h['name']}*.json"):
                s = json.loads(cand.read_text(encoding="utf-8"))
                if s.get("topic") == h.get("topic") or not script:
                    script = s
            m = rows.get(vid, {})
            videos.append({"name": h["name"], "video_id": vid, "topic": h.get("topic", ""),
                           "category": h.get("category") or script.get("category", ""),
                           "format": script.get("format", "standard"), "writer": script.get("writer", ""),
                           **{k: m.get(k) for k in METRICS}})
    known = {v["video_id"] for v in videos}
    others = [vid for vid in rows if vid not in known]  # e.g. videos posted by hand
    try:
        titles = _titles(others)
    except Exception as e:
        print(f"[analytics] titles skipped: {e}")
        titles = {}
    for vid in others:
        m = rows[vid]
        videos.append({"name": "", "video_id": vid, "topic": re.sub(r"\s*#\S+", "", titles.get(vid, vid)),
                       "category": "", "format": "手動投稿", "writer": "", **{k: m.get(k) for k in METRICS}})
    for v in videos:
        if v.get("averageViewPercentage") is not None:
            v["averageViewPercentage"] = round(v["averageViewPercentage"], 1)
    measured = [v for v in videos if (v.get("views") or 0) >= MIN_VIEWS]

    def group(key):
        out = {}
        for v in measured:
            g = out.setdefault(v[key] or "-", {"videos": 0, "views": 0, "avp": 0.0})
            g["videos"] += 1
            g["views"] += v["views"]
            g["avp"] += v["averageViewPercentage"] or 0
        for g in out.values():
            g["avg_views"] = round(g["views"] / g["videos"], 1)
            g["avg_view_percentage"] = round(g["avp"] / g["videos"], 1)
            del g["avp"]
        return out

    report = {"generated": dt.datetime.now().isoformat(timespec="minutes"), "videos": videos,
              "by_format": group("format"), "by_category": group("category"),
              "measured": len(measured)}
    d = Path(root) / "analytics"
    d.mkdir(exist_ok=True)
    (d / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "report.md").write_text(to_markdown(report), encoding="utf-8")
    return report


def to_markdown(r):
    L = [f"# チャンネル分析（{r['generated']} 時点）", "",
         f"集計対象: 再生{MIN_VIEWS}回以上の {r['measured']} 本（非公開・公開直後の動画は除外。YouTube の集計は約2日遅れ）", ""]
    for title, key in (("形式別", "by_format"), ("カテゴリ別", "by_category")):
        L += [f"## {title}", "", "| | 本数 | 平均再生数 | 平均視聴率 |", "|---|---|---|---|"]
        for k, g in sorted(r[key].items(), key=lambda x: -x[1]["avg_views"]):
            L.append(f"| {k} | {g['videos']} | {g['avg_views']} | {g['avg_view_percentage']}% |")
        L.append("")
    L += ["## 動画別", "", "| 動画 | 形式 | 再生 | 視聴率 | 平均視聴秒 | 高評価 | コメント | 登録者+ |",
          "|---|---|---|---|---|---|---|---|"]
    for v in sorted(r["videos"], key=lambda v: -(v.get("views") or 0)):
        L.append(f"| {v['topic'][:24]} | {v['format']} | {v.get('views') or '-'} | "
                 f"{v.get('averageViewPercentage') or '-'}% | {v.get('averageViewDuration') or '-'} | "
                 f"{v.get('likes') or '-'} | {v.get('comments') or '-'} | {v.get('subscribersGained') or '-'} |")
    return "\n".join(L) + "\n"


def tuned_weights(base, groups):
    """Scale base weights by each group's score relative to the mean (views x view percentage)."""
    scored = {k: g["avg_views"] * (g["avg_view_percentage"] / 100 + 0.2)
              for k, g in groups.items() if g["videos"] >= MIN_VIDEOS}
    if len(scored) < 2:
        return dict(base)
    mean = sum(scored.values()) / len(scored)
    out = {}
    for k, w in base.items():
        if k in scored and mean > 0:
            f = max(WEIGHT_RANGE[0], min(WEIGHT_RANGE[1], scored[k] / mean))
            out[k] = round(w * f, 3)
        else:
            out[k] = w
    return out
