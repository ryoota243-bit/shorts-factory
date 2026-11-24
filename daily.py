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

from factory.render import render

ROOT = Path(__file__).resolve().parent
JST = dt.timezone(dt.timedelta(hours=9))


def main():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=cfg.get("daily", {}).get("count", 1))
    a = ap.parse_args()

    hist_path = ROOT / "history.json"
    history = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else []
    queue_dir, archive = ROOT / "scripts/queue", ROOT / "scripts/archive"
    archive.mkdir(parents=True, exist_ok=True)
    out = ROOT / "output"
    today = dt.datetime.now(JST).strftime("%Y-%m-%d")
    made = []

    used = {h["name"] for h in history} | {p.stem for p in archive.glob("*.json")}
    n = 0
    for i in range(a.count):
        n += 1
        while f"{today}_{n:02d}" in used:
            n += 1
        name = f"{today}_{n:02d}"
        used.add(name)
        queue = sorted(queue_dir.glob("*.json"))
        if queue:
            script = json.loads(queue[0].read_text(encoding="utf-8"))
            queue[0].unlink()
            print(f"[daily] queue: {queue[0].name}")
        elif os.environ.get("ANTHROPIC_API_KEY"):
            from factory.generate import generate
            recent = [h.get("category") for h in history[-6:] if h.get("category")]
            script = generate([h["topic"] for h in history], cfg["outro"],
                              seed=dt.date.today().toordinal() * 10 + i, recent_categories=recent)
        else:
            print("[daily] queue is empty and ANTHROPIC_API_KEY is not set; nothing to do")
            break
        path = archive / f"{name}.json"
        path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
        render(path, cfg, ROOT, out)
        history.append({"date": today, "name": name, "category": script.get("category", ""),
                        "topic": script.get("topic") or script.get("title", "").replace("[", "").replace("]", "")})
        made.append(name)
        hist_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    # release notes: copy-paste friendly upload info
    lines = []
    for n in made:
        m = json.loads((out / f"{n}.json").read_text(encoding="utf-8"))
        lines += [f"## {n}.mp4", "", "**タイトル**", "```", m["title"] or "", "```", "**概要欄**", "```",
                  m["description"], "```", "**タグ**: " + ", ".join(m["tags"]), ""]
        if m.get("sources"):
            lines += ["<details><summary>出典（事実確認用）</summary>", ""]
            lines += [f"- {s}" for s in m["sources"]] + ["", "</details>", ""]
        if m.get("credits"):
            lines += ["素材: " + " / ".join(m["credits"]), ""]
        if "openjtalk" in m.get("tts", []) and cfg["tts"]["backend"] != "openjtalk":
            lines += ["⚠️ VOICEVOX に接続できず、一部 Open JTalk の声で生成されています", ""]
    (out / "RELEASE_NOTES.md").write_text("\n".join(lines) or "生成なし", encoding="utf-8")
    print(f"[daily] made: {made}")


if __name__ == "__main__":
    main()
