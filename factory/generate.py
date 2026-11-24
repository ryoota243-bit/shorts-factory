"""Write a new trivia script with Claude (web-search fact check -> structured JSON)."""
import datetime as dt
import json
import random
from typing import List, Literal

import anthropic
from pydantic import BaseModel

MODEL = "claude-opus-5-5"

CATEGORIES = ["動物", "人体", "宇宙", "歴史", "食べ物", "日本の文化", "科学", "言葉・語源",
              "海の生き物", "植物", "地理・世界の国", "身近なモノ", "昆虫", "スポーツ", "お金・経済"]


class Item(BaseModel):
    emoji: str
    text: str
    label: str
    value: str
    color: str
    note: str


class Segment(BaseModel):
    label: str
    value: float
    color: str


class Visual(BaseModel):
    type: Literal["photo", "video", "ai", "illustration", "number", "compare", "list", "pie", "text"]
    emoji: str
    text: str
    query: str
    ai_prompt: str
    number: float
    unit: str
    label: str
    center: str
    items: List[Item]
    segments: List[Segment]


class Scene(BaseModel):
    narration: str
    visual: Visual


class Pron(BaseModel):
    word: str
    reading: str


class Script(BaseModel):
    topic: str
    title: str
    youtube_title: str
    description: str
    tags: List[str]
    bg_colors: List[str]
    accent: str
    pronunciations: List[Pron]
    scenes: List[Scene]
    sources: List[str]


RESEARCH_PROMPT = """あなたは日本語YouTubeショートの雑学チャンネルの構成作家です。
カテゴリ「{category}」から、視聴者が「へぇ！」と驚く雑学ネタを1つ選び、35〜50秒の台本を作ってください。

# 重複禁止（過去に扱ったテーマ）
{history}

# 手順
1. web検索で、選んだネタの事実を信頼できる情報源（公的機関・大学・百科事典・専門メディア）で確認する。
   俗説・諸説あるもの・確認できないものは採用しない。数字は出典どおりに。
2. 確認できた事実だけで台本を書く。

# 台本の条件
- 構成: 1シーン目は強いフック（疑問や意外性）。2〜6シーン目で事実を順に説明。最後は短いオチ。
  フォローの呼びかけは不要（自動で追加される）。
- シーン数5〜7、ナレーション合計200〜300文字。1シーンのナレーションは1〜2文。
- ナレーションは話し言葉。強調したい語句は [ ] で囲む（各シーン1か所まで）。
- 各シーンに映像を指定:
  photo（実写写真。query は素材サイトで確実に見つかる一般的な英語2〜3語。固有名詞・年代・型番は使わず、
  例: "IBM 1401" ではなく "vintage computer"。AI生成用の英語 ai_prompt も書く）/
  number（数字を大きく）/ compare（2つの比較）/ list（2〜4項目）/ pie（割合）/
  illustration（絵文字1〜2個＋短い文字）/ text（大きな文字）。
  photo は全体の3〜4割、図解系（number/compare/list/pie）を最低2つ入れる。
- 画面上部のタイトル（title）は15文字以内、[ ]で1か所強調。
- youtube_title は末尾に #雑学 #shorts。description は2〜3行＋ハッシュタグ。
- 画面の文字は短く: compare の label は8文字・value は6文字・note は14文字まで、
  pie の center は6文字まで（例「約3割」）、list の各 text は14文字まで、illustration/text の text は12文字まで。
- 画面に出す文字（text, label, items の text など）に絵文字は入れない。絵文字は emoji 欄だけに。
- 読み間違えやすい語（数字＋助数詞、難読語）は pronunciations に読みをひらがなで。

最後に、使った事実・数字と出典URLを箇条書きでまとめてください。"""

FORMAT_PROMPT = """次の台本案を、指定のJSONスキーマに変換してください。
- 使わない項目は空文字・空配列・0 にする。
- visual.type ごとに使う項目:
  photo: query, ai_prompt, emoji, text（emoji と text は写真が見つからない時の代わりのイラスト用）
  number: number, unit, label / compare: items(2件: label, value, color, note, emoji)
  list: items(emoji, text) / pie: segments(label, value, color), center
  illustration: emoji, text / text: text, emoji
- color は #RRGGBB。bg_colors は背景グラデーション用の暗めの2色。accent は明るい強調色。
- sources は出典URLの配列。topic はテーマを短く。

# 台本案
{draft}"""


def _text(msg):
    return "".join(b.text for b in msg.content if b.type == "text")


def research(client, category, history):
    hist = "\n".join(f"- {h}" for h in history[-200:]) or "（なし）"
    messages = [{"role": "user", "content": RESEARCH_PROMPT.format(category=category, history=hist)}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]
    for _ in range(5):
        msg = client.beta.messages.create(
            model=MODEL, max_tokens=16000, messages=messages, tools=tools,
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        if msg.stop_reason == "refusal":
            raise RuntimeError(f"refused: {msg.stop_details}")
        if msg.stop_reason != "pause_turn":
            return _text(msg)
        messages = messages[:1] + [{"role": "assistant", "content": msg.content}]
    raise RuntimeError("research did not finish")


def to_script(client, draft):
    res = client.messages.parse(
        model=MODEL, max_tokens=16000, output_format=Script,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": FORMAT_PROMPT.format(draft=draft)}],
    )
    if res.stop_reason == "refusal":
        raise RuntimeError(f"refused: {res.stop_details}")
    return res.parsed_output


def _visual(v: Visual):
    t = v.type
    if t in ("photo", "video", "ai"):
        return {"type": t, "query": v.query, "ai_prompt": v.ai_prompt,
                "fallback": {"type": "illustration", "emoji": v.emoji or "🔍", "text": v.text}}
    if t == "number":
        n = int(v.number) if float(v.number).is_integer() else v.number
        return {"type": "number", "value": n, "unit": v.unit, "label": v.label}
    if t == "compare":
        return {"type": "compare", "items": [
            {k: getattr(i, k) for k in ("label", "value", "color", "note", "emoji") if getattr(i, k)}
            for i in v.items[:2]]}
    if t == "list":
        return {"type": "list", "items": [{"emoji": i.emoji, "text": i.text} for i in v.items[:4]]}
    if t == "pie":
        return {"type": "pie", "center": v.center,
                "segments": [{"label": s.label, "value": s.value, "color": s.color} for s in v.segments]}
    if t == "illustration":
        return {"type": "illustration", "emoji": v.emoji or "💡", "text": v.text}
    return {"type": "text", "text": v.text, "emoji": v.emoji}


def generate(history, outro, seed=None, recent_categories=()):
    client = anthropic.Anthropic()
    rng = random.Random(seed or dt.date.today().toordinal())
    pool = [c for c in CATEGORIES if c not in set(recent_categories)] or CATEGORIES
    category = rng.choice(pool)
    print(f"[generate] category: {category}")
    draft = research(client, category, history)
    s = to_script(client, draft)
    desc = s.description
    return {
        "topic": s.topic,
        "category": category,
        "title": s.title,
        "youtube_title": s.youtube_title,
        "description": desc,
        "tags": s.tags,
        "theme": {"bg": (s.bg_colors + ["#1e3c72", "#6a1b9a"])[:2], "accent": s.accent or "#ffd600"},
        "pronunciations": {p.word: p.reading for p in s.pronunciations},
        "scenes": [{"narration": sc.narration, "visual": _visual(sc.visual)} for sc in s.scenes] + [outro],
        "sources": s.sources,
    }
