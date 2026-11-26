#!/usr/bin/env python3
"""毎日の自動生成: scripts/queue の台本を優先し、なければ Claude で新しい台本を作って動画化する。

  python daily.py              # config.json の daily.count 本
  python daily.py --count 3
"""
import argparse
import datetime as dt
import json
import os
import shutil
from pathlib import Path

from factory import gemini
from factory.render import render

ROOT = Path(__file__).resolve().parent
JST = dt.timezone(dt.timedelta(hours=9))


def main():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=cfg.get("daily", {}).get("count", 1))
    ap.add_argument("--writer", choices=["claude", "gemini", "gemini+claude"], default=cfg.get("script_writer", "claude"))
    ap.add_argument("--compare", action="store_true",
                    help="同じお題を Claude と Gemini の両方で台本化して2本ずつ作る")
    ap.add_argument("--rerender", nargs="*", default=[],
                    help="scripts/archive の既存台本で動画だけ作り直す（例: 2026-11-24_12）")
    ap.add_argument("--upload", action="store_true", help="作った動画を YouTube にアップロードする")
    ap.add_argument("--analytics-only", action="store_true", help="YouTube の分析レポートだけ更新する")
    ap.add_argument("--style", choices=["", "kamishibai"], default="", help="映像スタイル（kamishibai=紙芝居・手描き風）")
    ap.add_argument("--format", choices=["standard", "quiz", "myth"], help="台本の形式を指定（省略時は日替わり）")
    ap.add_argument("--upload-only", nargs="*", default=[],
                    help="output/ にある完成済みの動画（例: 2026-11-24_08_gemini）をアップロードだけする")
    a = ap.parse_args()
    gemini.configure(cfg)

    hist_path = ROOT / "history.json"
    history = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else []
    queue_dir, archive = ROOT / "scripts/queue", ROOT / "scripts/archive"
    archive.mkdir(parents=True, exist_ok=True)
    out = ROOT / "output"
    today = dt.datetime.now(JST).strftime("%Y-%m-%d")
    made = []

    used = {h["name"] for h in history} | {p.stem for p in archive.glob("*.json")}
    def next_name():
        n = 1
        while f"{today}_{n:02d}" in used:
            n += 1
        return f"{today}_{n:02d}"

    def make(script, name):
        path = archive / f"{name}.json"
        path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
        used.add(name)
        try:
            render(path, cfg, ROOT, out)
            made.append(name)
        except Exception as e:  # keep going with the next video
            print(f"[daily] render failed for {name}: {e}")

    tuned = {}
    if not a.upload_only and not a.rerender and cfg.get("analytics", {}).get("enabled", True):
        from factory import youtube as _yt
        if _yt.configured():
            try:
                from factory import analytics
                from factory.generate import FORMAT_WEIGHTS, CATEGORIES
                rep = analytics.build(ROOT, history)
                tuned = {"format_weights": analytics.tuned_weights(FORMAT_WEIGHTS, rep["by_format"]),
                         "category_weights": analytics.tuned_weights({c: 1.0 for c in CATEGORIES},
                                                                     rep["by_category"])}
                print(f"[analytics] measured {rep['measured']} videos; format weights {tuned['format_weights']}")
            except Exception as e:
                print(f"[analytics] skipped: {e}")
    if a.analytics_only:
        return

    if a.upload_only:  # upload already-rendered videos (downloaded from Releases by the workflow)
        from factory.render import build_description, normalize
        names = [n for n in a.upload_only if (out / f"{n}.mp4").exists() and (out / f"{n}.json").exists()]
        for n in names:  # refresh the description from the current script (latest footer / fixes)
            sp = archive / f"{n}.json"
            if sp.exists():
                m = json.loads((out / f"{n}.json").read_text(encoding="utf-8"))
                m["description"] = build_description(normalize(json.loads(sp.read_text(encoding="utf-8"))), cfg,
                                                     voicevox="voicevox" in m.get("tts", ["voicevox"]))
                (out / f"{n}.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        missing = sorted(set(a.upload_only) - set(names))
        if missing:
            print(f"[daily] not found in output/: {missing}")
        upload_all(names, out, cfg, history, hist_path)
        (out / "UPLOAD_ONLY").write_text("\n".join(names), encoding="utf-8")
        return

    for name in a.rerender:  # re-render existing scripts with the latest code (no new script cost)
        path = archive / f"{name}.json"
        if not path.exists():
            print(f"[daily] no such script: {name}")
            continue
        try:
            render(path, cfg, ROOT, out)
            made.append(name)
        except Exception as e:
            print(f"[daily] render failed for {name}: {e}")

    for i in range(0 if a.rerender else a.count):
        seed = dt.date.today().toordinal() * 10 + i + len(history)
        recent = [h.get("category") for h in history[-6:] if h.get("category")]
        queue = sorted(queue_dir.glob("*.json"))
        if queue and not a.compare:
            script = json.loads(queue[0].read_text(encoding="utf-8"))
            queue[0].unlink()
            print(f"[daily] queue: {queue[0].name}")
            scripts = [(script, "")]
        elif os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY"):
            from factory.generate import generate
            topics = [h["topic"] for h in history]
            writers = ["claude", "gemini"] if a.compare else [a.writer]
            scripts = []
            first = None
            for w in writers:
                try:
                    if first is None:
                        s = generate(topics, cfg["outro"], seed=seed, recent_categories=recent, writer=w,
                                     fmt=a.format, style=a.style, **tuned)
                        first = s
                    else:  # same topic & category as the first writer
                        s = generate(topics, cfg["outro"], writer=w, fmt=first.get("format"), style=a.style,
                                     category=first["category"], topic=first["topic"])
                    scripts.append((s, f"_{w}" if a.compare else ""))
                except Exception as e:
                    print(f"[daily] {w} script failed: {e}")
            if not scripts:
                continue
        else:
            print("[daily] queue is empty and no API key is set; nothing to do")
            break
        base = next_name()
        for script, suffix in scripts:
            make(script, base + suffix)
        used.add(base)
        s0 = scripts[0][0]
        history.append({"date": today, "name": base, "category": s0.get("category", ""),
                        "topic": s0.get("topic") or s0.get("title", "").replace("[", "").replace("]", "")})
        hist_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    if a.upload and made:
        upload_all(made, out, cfg, history, hist_path)
    write_notes(made, out, cfg)
    print(f"[daily] made: {made}")


def upload_all(names, out, cfg, history, hist_path):
    from factory import youtube
    if not cfg.get("youtube", {}).get("enabled", True):
        return
    if not youtube.configured():
        print("[youtube] secrets not set; skipping upload")
        return
    for n in names:
        meta_path = out / f"{n}.json"
        m = json.loads(meta_path.read_text(encoding="utf-8"))
        try:
            r = youtube.upload(out / f"{n}.mp4", m, cfg, out / f"{n}.thumb.png")
            print(f"[youtube] {n}: {r['url']} ({r['privacy']}) {r['note']}")
        except Exception as e:
            r = {"error": str(e)}
            print(f"[youtube] {n}: upload failed: {e}")
        m["youtube"] = r
        meta_path.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        for h in history:
            if h["name"] == n or n.startswith(h["name"]):
                h.setdefault("youtube", []).append(r.get("url") or r.get("error"))
    hist_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def notes_for(names, out, cfg):
    """Copy-paste friendly upload info for the release page."""
    lines = []
    for n in names:
        m = json.loads((out / f"{n}.json").read_text(encoding="utf-8"))
        yt = m.get("youtube") or {}
        yt_line = (f"**YouTube: {yt['url']}（{'非公開' if yt.get('privacy') == 'private' else yt.get('privacy')}）**"
                   + (f" / {yt['note']}" if yt.get("note") else "") if yt.get("url")
                   else (f"⚠️ YouTube へのアップロード失敗: {yt['error']}" if yt.get("error") else ""))
        lines += [f"## {n}.mp4", "", yt_line, "", f"台本: {m.get('writer') or '手動'}", "",
                  (f"サムネイル: {m['thumbnail']}（動画の最初のコマも同じ画面です。アプリでカバーに選べます）"
                   if m.get("thumbnail") else ""), "", "**タイトル**", "```", m["title"] or "", "```",
                  "**概要欄**", "```", m["description"], "```", "**タグ**: " + ", ".join(m["tags"]), ""]
        if m.get("factcheck"):
            lines += ["**Claude 校閲メモ**", ""] + [f"- {x}" for x in m["factcheck"]] + [""]
        if m.get("qa"):
            lines += ["**自動チェックで見つかった問題**", ""]
            lines += [f"- [{q['severity']}] シーン{q['scene']}: {q['issue']}" for q in m["qa"]] + [""]
        if m.get("sources"):
            lines += ["<details><summary>出典（事実確認用）</summary>", ""]
            lines += [f"- {x}" for x in m["sources"]] + ["", "</details>", ""]
        if m.get("credits"):
            lines += ["素材: " + " / ".join(m["credits"]), ""]
        if "openjtalk" in m.get("tts", []) and cfg["tts"]["backend"] != "openjtalk":
            lines += ["⚠️ VOICEVOX に接続できず、一部 Open JTalk の声で生成されています", ""]
    return "\n".join(lines) or "生成なし"


def write_notes(names, out, cfg):
    (out / "RELEASE_NOTES.md").write_text(notes_for(names, out, cfg), encoding="utf-8")


if __name__ == "__main__":
    main()
