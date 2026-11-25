"""Ask Claude whether an image fits the scene before it goes into the video."""
import base64
import io
import os
import subprocess

from PIL import Image
from pydantic import BaseModel

MODEL = "claude-opus-5-5"


class Verdict(BaseModel):
    ok: bool
    reason: str


PROMPT = """日本語の雑学ショート動画の1場面に、この画像を背景として使う予定です。
動画のテーマ: {topic}
ナレーション: {narration}
画像に求める内容: {want}

視聴者に誤解を与えないかを判定してください。次のどれかに当たれば ok=false:
- ナレーションで話している具体的な物・場所・生き物と、明らかに別のものが写っている
  （例: 国際宇宙ステーションの話なのにソユーズ宇宙船、フランス領ギアナの話なのにイグアスの滝、
   ボールペンの話なのに万年筆）
- 場面と無関係で、何の画像か分からない
- 日本の事柄の話なのに、外国の通貨（紙幣・硬貨）・外国語の看板や文字・外国の国旗や建物など、
  別の国のものだと分かる要素が写っている（例: 日本の紙幣の話なのに米ドル札）
- 実在の紙幣・硬貨の図柄がはっきり写っている（どの国のものでも、誤解や複製の懸念があるため）
- 実在の有名人の顔、または実在の人物が主役として写り顔が判別できる写真（特定の職業の本人など）、
  目立つ文字・ロゴ・透かし、不快・不適切な内容
雰囲気が合っていて誤解を招かない一般的な画像なら ok=true。reason は短く日本語で。"""


def _thumb(path):
    p = str(path)
    if p.lower().endswith((".mp4", ".mov", ".webm")):
        raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", "1", "-i", p, "-frames:v", "1",
                              "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
        img = Image.open(io.BytesIO(raw))
    else:
        img = Image.open(p)
    img = img.convert("RGB")
    img.thumbnail((640, 640))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode()


def check(path, narration, want, topic=""):
    """Return (ok, reason). Without an API key every image passes."""
    if not os.environ.get("ANTHROPIC_API_KEY") or not narration:
        return True, ""
    import anthropic
    res = anthropic.Anthropic().messages.parse(
        model=MODEL, max_tokens=2000, output_format=Verdict, output_config={"effort": "low"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _thumb(path)}},
            {"type": "text", "text": PROMPT.format(narration=narration, want=want, topic=topic or "（不明）")}]}])
    v = res.parsed_output
    if v is None:
        return True, ""
    return v.ok, v.reason
