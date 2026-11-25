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
    thumb_text: str
    thumb_prompt: str


RESEARCH_PROMPT = """あなたは日本語YouTubeショートの雑学チャンネルの構成作家です。
{theme_line}

# 手順
1. web検索で、ネタの事実を信頼できる情報源（公的機関・大学・百科事典・専門メディア）で確認する。
   俗説・諸説あるもの・確認できないものは採用しない。数字は出典どおりに。
2. 確認できた事実だけで台本を書く。

# 台本の条件
- 構成: 1シーン目（開始3秒）が最重要。最初の一言（最初の句読点まで）は10文字前後で、一番意外な事実や
  数字をいきなり言い切る（例:「雲1つで、500トン。」「タコの心臓は、3つ。」）。「〜って知っていますか？」
  「実は〜」のような前置きから始めない。1シーン目のナレーションは35文字以内で、映像は photo か ai の
  一目で引き込まれる印象的な画像にする。thumb_text（キャッチコピー）は開始直後に画面に大きく出るので、
  最初の一言と同じ驚きを短く表す。2〜6シーン目で事実を順に説明。最後は短いオチ。
  フォローの呼びかけは不要（自動で追加される）。
- 同じ結論や同じ言い回しを2回繰り返さない。同じ種類の映像（例: number と number）を2シーン続けない
  （2つの数字を並べたいときは1つの compare にまとめる）。
- emoji は内容をそのまま表すものを選ぶ（例: ピラミッドに 🏛️ は使わない。ぴったりの絵文字が無いときは
  🏜️ のように場所や雰囲気を表すものにする）。数字・英字は半角で書く。
- シーン数5〜8、ナレーション合計200〜300文字。1シーンのナレーションは1〜2文・45文字以内
  （長くなるならシーンを分ける。同じ画面が6秒以上続かないように）。
- ナレーションは話し言葉。強調したい語句は [ ] で囲む（各シーン1か所まで）。
- 各シーンに映像を指定:
  photo（素材サイトの実写写真。どこにでもある一般的なモノ・風景・動物向け。query は確実に見つかる
  一般的な英語2〜3語で、固有名詞・年代・型番は使わない。例: "IBM 1401" ではなく "vintage computer"）/
  ai（AI生成画像。素材サイトに無さそうな具体的な場面向け: 歴史の一場面、発明の瞬間、特定の状況、
  比喩的なイメージなど）/
  photo と ai の両方に、英語の ai_prompt（写実的な写真風。画像内に文字を入れない。実在の人物の顔は描かない）を書く /
  number（数字を大きく。そのシーンのナレーションはその数字の話だけにする）/
  compare（性質の違う2つの対比だけ。同じ量の言い換え（500t＝ゾウ100頭）には使わない）/ list（2〜4項目）/
  pie（「全体のうち何割か」を示す時だけ。A は B の何割、のような別物同士の比較には compare を使う）/
  illustration（絵文字1〜2個＋短い文字）/ text（大きな文字）。
  photo と ai を合わせて全体の3〜4割、図解系（number/compare/list/pie）を最低2つ入れる。
- 画面上部のタイトル（title）は15文字以内、[ ]で1か所強調。
- サムネイル: thumb_text は思わず見たくなる8〜12文字のキャッチコピー（疑問や驚き。[ ]で1か所強調。
  内容と食い違う誇張はしない）。thumb_prompt はサムネイル背景用の英語の画像指示（主役を大きく、
  印象的で高コントラスト、画像内に文字を入れない、実在の人物の顔は描かない）。
- youtube_title は末尾に #雑学 #shorts。description は2〜3行＋ハッシュタグ。
- number の label は数字の説明として単独で意味が通る見出しにする（例「対象年齢」「必要な空気量」。
  「までの〜」「以上の〜」のように数字の続きから始めない）。
- お金の話でも、紙幣・硬貨そのものは画像にしない（AI も素材サイトも日本の紙幣を正しく出せず、外国の
  通貨が写って誤解を招くため）。財布・ATM・銀行の窓口・電卓など一般的な情景にする。
- 写真や AI 画像は、特定の機体・建物・場所の正確な見た目が必要なもの（例: 国際宇宙ステーション、特定の滝）は
  避け、一般的な情景（例: 宇宙から見た地球と日の出、熱帯雨林の川）にする。
- 画面の文字は短く: compare の label は8文字・value は6文字・note は12文字まで、
  pie の center は6文字まで（例「約3割」）、list の各 text は14文字まで、illustration/text の text は12文字まで。
- 画面に出す文字（text, label, items の text など）に絵文字は入れない。絵文字は emoji 欄だけに。
- pronunciations には、難読語・固有名詞・外国語の略語など読み間違えやすい語だけを、
  ひらがなの読みで入れる（一般的な語や「1年」「2回」のような数字は入れない。1文字の英字は入れない）。

最後に、使った事実・数字と出典URLを箇条書きでまとめてください。"""

FORMAT_PROMPT = """次の台本案を、指定のJSONスキーマに変換してください。
（校閲レポートの場合は、修正を反映して書き直した最終版の台本を使ってください。）
- 使わない項目は空文字・空配列・0 にする。
- visual.type ごとに使う項目:
  photo / ai: query, ai_prompt, emoji, text（emoji と text は画像が用意できない時の代わりのイラスト用）
  number: number, unit, label / compare: items(2件: label, value, color, note, emoji)
  list: items(emoji, text) / pie: segments(label, value, color), center
  illustration: emoji, text / text: text, emoji
- color は #RRGGBB。bg_colors は背景グラデーション用の暗めの2色。accent は明るい強調色。
- sources は出典URLの配列。topic はテーマを短く。thumb_text / thumb_prompt はサムネイル用。

# 台本案
{draft}"""


def _text(msg):
    return "".join(b.text for b in msg.content if b.type == "text")


def research(client, prompt, max_uses=8):
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses}]
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


def theme_line(category=None, history=(), topic=None):
    if topic:
        return (f"テーマ「{topic}」（カテゴリ: {category}）の雑学で、視聴者が「へぇ！」と驚く"
                f"35〜50秒の台本を作ってください。")
    hist = "\n".join(f"- {h}" for h in list(history)[-200:]) or "（なし）"
    return (f"カテゴリ「{category}」から、視聴者が「へぇ！」と驚く雑学ネタを1つ選び、35〜50秒の台本を"
            f"作ってください。\n\n# 重複禁止（過去に扱ったテーマ）\n{hist}")


def assemble(s: Script, category, outro, writer):
    return {
        "topic": s.topic,
        "category": category,
        "writer": writer,
        "title": s.title,
        "youtube_title": s.youtube_title,
        "description": s.description,
        "tags": s.tags,
        "theme": {"bg": (s.bg_colors + ["#1e3c72", "#6a1b9a"])[:2], "accent": s.accent or "#ffd600"},
        "pronunciations": {p.word: p.reading for p in s.pronunciations},
        "scenes": [{"narration": sc.narration, "visual": _visual(sc.visual)} for sc in s.scenes] + [outro],
        "sources": s.sources,
        "thumb_text": s.thumb_text,
        "thumb_prompt": s.thumb_prompt,
    }


def pick_category(seed=None, recent_categories=()):
    rng = random.Random(seed or dt.date.today().toordinal())
    pool = [c for c in CATEGORIES if c not in set(recent_categories)] or CATEGORIES
    return rng.choice(pool)


def generate(history, outro, seed=None, recent_categories=(), writer="claude", category=None, topic=None):
    """Write a script. With topic set, write about that topic instead of choosing one."""
    category = category or pick_category(seed, recent_categories)
    print(f"[generate:{writer}] category: {category}" + (f" / topic: {topic}" if topic else ""))
    prompt = RESEARCH_PROMPT.format(theme_line=theme_line(category, history, topic))
    notes = None
    if writer in ("gemini", "gemini+claude"):
        from . import gemini
        s = gemini.write_script(prompt, FORMAT_PROMPT, Script)
        if writer == "gemini+claude":
            from .factcheck import check
            print("[generate] Claude fact-check...")
            s, notes = check(s)
            for n in notes:
                print(f"  [factcheck] {n}")
    else:
        client = anthropic.Anthropic()
        s = to_script(client, research(client, prompt))
    out = assemble(s, category, outro, writer)
    if notes is not None:
        out["factcheck"] = notes
    return out
