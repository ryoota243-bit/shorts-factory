#!/usr/bin/env python3
"""Usage:
  python make.py scripts/octopus.json        # 1本
  python make.py scripts/                    # フォルダ内の台本を全部
  python make.py scripts/ --tts voicevox     # 音声エンジンを切り替え
"""
import argparse
import json
from pathlib import Path

from factory.render import render

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="台本JSON、またはJSONの入ったフォルダ")
    ap.add_argument("--tts", help="openjtalk / voicevox / google")
    ap.add_argument("--out", default=str(ROOT / "output"))
    ap.add_argument("--skip-existing", action="store_true")
    a = ap.parse_args()
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    if a.tts:
        cfg["tts"]["backend"] = a.tts
    t = Path(a.target)
    files = sorted(t.glob("*.json")) if t.is_dir() else [t]
    for f in files:
        if a.skip_existing and (Path(a.out) / f"{f.stem}.mp4").exists():
            continue
        render(f, cfg, ROOT, a.out)


if __name__ == "__main__":
    main()
