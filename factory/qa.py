"""Automatic quality check of a rendered video (Claude looks at sampled frames + the script)."""
import base64
import json
import subprocess
from pathlib import Path
from typing import List, Literal

import anthropic
from pydantic import BaseModel

MODEL = "claude-opus-5-5"
COLS, ROWS = 6, 3


class Issue(BaseModel):
    scene: int
    severity: Literal["高", "中", "低"]
    issue: str
    suggestion: str


class QA(BaseModel):
    issues: List[Issue]


PROMPT = """これは日本語の雑学ショート動画（縦型）から等間隔に取り出した{n}コマを、左上から右へ順に
{cols}列×{rows}行に並べた画像です。各コマの情報と台本を参考に、視聴者目線で問題点を洗い出してください。

# 見るポイント
- 文字のはみ出し・欠け・重なり・文字化け（□や?）・読めないほど小さい文字
- 字幕の不自然な改行（単語や数字の途中、かっこの内側）、字幕と画面要素の重なり
- 画像・図解の内容がナレーションと合っていない、誤解を招く図（円グラフの使い方など）
- 事実の明らかな誤り、言い過ぎ、ナレーションと画面の数字の食い違い
- 単調さ（同じ見た目が長く続く）、冒頭のつかみの弱さ
- 実在の人物の顔、ロゴ、不適切な画像

# 判断の前提
- 一覧画像の各コマは実寸（幅1080px）の約1/4.5に縮小されている。実寸で文字の高さ40px以上
  （縮小後で約9px以上）はスマホで読める大きさとみなす。
- フェード・スライド・拡大などのアニメーション途中のコマや、シーン切り替え（約0.2秒のクロスフェード）
  のコマで要素が半透明・移動途中なのは正常。崩れとして指摘しない。
- 数字のカウントアップ途中の値が見えるのは演出で正常。

軽微な好みの問題は「低」、視聴者が気づく見た目の崩れや分かりにくさは「中」、
事実の誤り・文字化け・大きな崩れは「高」。問題がなければ issues は空配列。
scene はシーン番号（1始まり、最後のフォロー呼びかけも1シーンと数える）。

# 各コマ
{frames}

# 台本
{script}"""


def _sheet(mp4, duration, out_png):
    n = COLS * ROWS
    vf = f"fps={n}/{duration},scale=240:426,tile={COLS}x{ROWS}"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(mp4), "-vf", vf,
                    "-frames:v", "1", str(out_png)], check=True)
    return [(k + 0.5) * duration / n for k in range(n)]


def review(mp4, script, scenes_t, subs):
    """Return a list of issue dicts (empty when nothing found)."""
    duration = scenes_t[-1][1]
    png = Path(mp4).with_suffix(".sheet.png")
    times = _sheet(mp4, duration, png)
    lines = []
    for k, t in enumerate(times):
        sc = next((i for i, (a, b) in enumerate(scenes_t) if a <= t < b), len(scenes_t) - 1)
        sub = next((s for a, b, s in subs if a - 0.05 <= t < b + 0.1), "")
        lines.append(f"コマ{k + 1}: {t:.1f}秒 シーン{sc + 1} 字幕「{sub}」")
    img = base64.standard_b64encode(png.read_bytes()).decode()
    png.unlink()
    body = json.dumps({k: script[k] for k in ("title", "scenes") if k in script}, ensure_ascii=False)
    client = anthropic.Anthropic()
    res = client.messages.parse(
        model=MODEL, max_tokens=8000, output_format=QA, output_config={"effort": "medium"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img}},
            {"type": "text", "text": PROMPT.format(n=len(times), cols=COLS, rows=ROWS,
                                                   frames="\n".join(lines), script=body)},
        ]}],
    )
    if res.stop_reason == "refusal" or res.parsed_output is None:
        return []
    order = {"高": 0, "中": 1, "低": 2}
    return [i.model_dump() for i in sorted(res.parsed_output.issues, key=lambda i: order[i.severity])]
