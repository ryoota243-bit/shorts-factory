"""Write a new trivia script with Claude (web-search fact check -> structured JSON)."""
import datetime as dt
import json
import random
import re
from typing import List, Literal

import anthropic

from . import llm
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
    type: Literal["photo", "video", "ai", "illustration", "number", "compare", "list", "pie", "text",
                  "quiz", "quiz_answer"]
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
    relation: Literal["vs", "arrow", "equal", "plus", "not_equal", "none"]


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
    mood: Literal["danger", "nature", "water_space", "food", "history", "science", "fun"]
    pronunciations: List[Pron]
    scenes: List[Scene]
    sources: List[str]
    thumb_text: str
    thumb_prompt: str
    comment_prompt: str


FORMATS = {
    "standard": ("通常", """意外な事実を言い切って始め、理由や仕組みを順に明かしていく。"""),
    "quiz": ("クイズ", """3択クイズ形式。1シーン目で驚きの事実をにおわせつつ問題を出す（例:「タコの心臓、いくつ？」）。
  2シーン目は visual.type=quiz で、text に問題文（20文字以内）、items に選択肢3つ（各8文字以内、text のみ）、
  number に正解の番号（1〜3）。ナレーションは問題と3つの選択肢を読み上げ（選択肢や答えを [ ] で強調しない。
  答えがばれるため）、選択肢は「1番、〇〇。2番、〇〇。3番、〇〇。」のように句点で区切って読む。最後に「正解は…」とは言わない
  （このあと3秒のカウントダウンが自動で入る）。3シーン目は visual.type=quiz_answer（2シーン目と同じ text・
  items・number）で、ナレーションは「正解は〇〇！」と言い切る。その後のシーンで理由を説明する。
  不正解の選択肢も、もっともらしいものにする。"""),
    "myth": ("誤解を正す", """多くの人が信じている「よくある誤解」を正す形式。1シーン目で誤解を否定して言い切る
  （例:「ダチョウは砂に頭を埋めない。」）。どこから誤解が生まれたか→本当はどうか→なぜそうなのか、の順に説明する。
  誤解と真実の対比には compare を使ってよい（label に「誤解」「真実」）。誤解が実際に広く信じられていることは
  出典で確認できるものに限る。"""),
}
# 使う形式と出やすさ。"quiz" は定義だけ残して使わない（クイズ形式は廃止。過去の台本の作り直し用）
FORMAT_WEIGHTS = {"standard": 5, "myth": 2}


RESEARCH_PROMPT = """あなたは日本語YouTubeショートの{channel_kind}の構成作家です。
{theme_line}{genre_guide}

# 今回の形式：{format_name}
{format_guide}

# 手順
1. web検索で、ネタの事実を信頼できる情報源（公的機関・大学・百科事典・専門メディア）で確認する。
   俗説・諸説あるもの・確認できないものは採用しない。数字は出典どおりに。
2. 確認できた事実だけで台本を書く。

# 台本の条件
- 構成: 1シーン目（開始3秒）が最重要。最初の一言（最初の句読点まで）は10文字前後で、一番意外な事実や
  数字をいきなり言い切る（例:「雲1つで、500トン。」「タコの心臓は、3つ。」）。「〜って知っていますか？」
  「実は〜」のような前置きから始めない。1シーン目のナレーションは35文字以内で、映像は photo か ai の
  一目で引き込まれる印象的な画像にする。thumb_text（キャッチコピー）は開始直後に画面に大きく出るので、
  最初の一言と同じ驚きを短く表す。その後のシーンで事実を順に説明し、最後は短いオチ。
- 最後まで見たくなる仕掛け: 途中（2〜3シーン目）で「最後にもっと驚く事実がある」のように予告してもよいが、
  予告したら必ず最後に回収する（回収しない予告や大げさな煽りは禁止）。
- ループ: 最後のシーンは、冒頭の驚きの事実を短く言い直して締める完結した文にする（例: 冒頭「雲1つで、500トン。」
  → 最後「見上げた雲にも、500トンの水があるんです。」）。2周目の冒頭と自然につながると理想的。
  文を「、」や「だから」などで途中で止める終わり方は禁止。すべてのシーンのナレーションは「。」「！」「？」で終える。
- コメントの問いかけ（「コメントで教えて」など）はシーンのナレーションに書かない。comment_prompt にだけ書く。
- comment_prompt: 動画の最後に読み上げる、思わずコメントしたくなる短い問いかけ（20文字以内。
  例「知ってた？コメントで教えて」「あなたはどっち派？」「他に知ってる雑学ある？」）。フォローの呼びかけは
  自動で画面に出るので書かない。
- 同じ結論や同じ言い回しを2回繰り返さない。同じ種類の映像（例: number と number）を2シーン続けない
  （2つの数字を並べたいときは1つの compare にまとめる）。
- emoji は内容をそのまま表すものを選ぶ（例: ピラミッドに 🏛️ は使わない。ぴったりの絵文字が無いときは
  🏜️ のように場所や雰囲気を表すものにする）。数字・英字は半角で書く。
- 画面を単調にしない: 1シーンは約5秒（30文字前後）まで。説明が長くなるときはシーンを分け、前のシーンと
  違う種類の映像にする（photo/ai → 図解 → photo/ai のように交互に）。絵文字だけの illustration や
  文字だけの text は連続させず、それぞれ30文字以内の短いシーンにする。
- シーン数5〜8、ナレーション合計150〜220文字（30〜40秒。クイズ形式はカウントダウンが入るので130〜190文字）。1シーンのナレーションは1〜2文・35文字以内
  （長くなるならシーンを分ける。同じ画面が6秒以上続かないように）。
- ナレーションは話し言葉。強調したい語句は [ ] で囲む（各シーン1か所まで）。
- 各シーンに映像を指定:
  photo（素材サイトの実写写真。どこにでもある一般的なモノ・風景・動物向け。query は確実に見つかる
  一般的な英語2〜3語で、固有名詞・年代・型番は使わない。例: "IBM 1401" ではなく "vintage computer"）/
  ai（AI生成画像。素材サイトに無さそうな具体的な場面向け: 歴史の一場面、発明の瞬間、特定の状況、
  比喩的なイメージなど）/
  photo と ai の両方に、英語の ai_prompt（写実的な写真風。画像内に文字を入れない。実在の人物の顔は描かない）を書く /
  number（数字を大きく。そのシーンのナレーションはその数字の話だけにする）/
  compare（2つを並べて見せる。relation で2つの関係を必ず選ぶ: vs＝対決・対比（人間の血 vs タコの血）/
  arrow＝変化・時間の流れ（昔→今、原因→結果）/ equal＝同じ量の言い換え（500t＝ゾウ100頭分）/
  plus＝内訳・合計（昼＋夜で1周）/ not_equal＝誤解と真実 / none＝単に条件や種類を並べるだけ。
  迷ったら none。勝ち負けや対立がないのに vs を使わない）/ list（2〜4項目）/
  pie（「全体のうち何割か」を示す時だけ。A は B の何割、のような別物同士の比較には compare を使う）/
  illustration（絵文字1〜2個＋短い文字）/ text（大きな文字）/
  quiz・quiz_answer（クイズ形式のときだけ。使い方は「今回の形式」に従う）。
  photo と ai を合わせて全体の3〜4割、図解系（number/compare/list/pie）を最低2つ入れる。
- 画面上部のタイトル（title）は15文字以内、[ ]で1か所強調。
- サムネイル: thumb_text は思わず見たくなる8〜12文字のキャッチコピー（疑問や驚き。[ ]で1か所強調。
  内容と食い違う誇張はしない）。thumb_prompt はサムネイル背景用の英語の画像指示（主役を大きく、
  印象的で高コントラスト、画像内に文字を入れない、実在の人物の顔は描かない）。
- youtube_title: 検索されやすいテーマの言葉（例「タコ」「回転寿司」）を前半に入れ、結末を言い切らずに想像させる
  （例「タコの心臓はなぜ3つ？」）。内容と食い違う煽りや釣りはしない。本文は40文字以内、末尾に #雑学 #shorts。
  description は2〜3行＋ハッシュタグ。
- number の label は数字の説明として単独で意味が通る見出しにする（例「対象年齢」「必要な空気量」。
  「までの〜」「以上の〜」のように数字の続きから始めない）。
- お金の話でも、紙幣・硬貨そのものは画像にしない（AI も素材サイトも日本の紙幣を正しく出せず、外国の
  通貨が写って誤解を招くため）。財布・ATM・銀行の窓口・電卓など一般的な情景にする。
- 写真や AI 画像は、特定の機体・建物・場所の正確な見た目が必要なもの（例: 国際宇宙ステーション、特定の滝）は
  避け、一般的な情景（例: 宇宙から見た地球と日の出、熱帯雨林の川）にする。
- 画面の文字は短く: compare の label は8文字・value は6文字・note は12文字まで、
  pie の center は6文字まで（例「約3割」）、list の各 text は14文字まで、illustration/text の text は12文字まで。
- compare の2つの value は同じ単位・同じ物差しにする（「1」と「約50倍」のように混ぜない。
  例「約2mg」と「約100mg」、または「1倍」と「約50倍」）。
- illustration / text の text は、そのシーンのナレーションの言い回しをそのまま繰り返さず、
  要点を8文字前後の見出しにする（字幕と同じ文が二重に出るのを避けるため）。
- 画面に出す文字（text, label, items の text など）に絵文字は入れない。絵文字は emoji 欄だけに。
- pronunciations には、難読語・固有名詞・外国語の略語など読み間違えやすい語だけを、
  ひらがなの読みで入れる（一般的な語や「1年」「2回」のような数字は入れない。1文字の英字は入れない）。

最後に、使った事実・数字と出典URLを箇条書きでまとめてください。"""

FORMAT_PROMPT = """次の台本案を、指定のJSONスキーマに変換してください。
（校閲レポートの場合は、修正を反映して書き直した最終版の台本を使ってください。）
- 使わない項目は空文字・空配列・0 にする。
- visual.type ごとに使う項目:
  photo / ai: query, ai_prompt, emoji, text（emoji と text は画像が用意できない時の代わりのイラスト用）
  number: number, unit, label / compare: items(2件: label, value, color, note, emoji), relation
  （compare 以外の relation は none）
  list: items(emoji, text) / pie: segments(label, value, color), center
  illustration: emoji, text / text: text, emoji
  quiz / quiz_answer: text（問題文）, items（選択肢3つ。text のみ）, number（正解の番号 1〜3）
- comment_prompt は最後に読み上げる問いかけ。
- color は #RRGGBB。bg_colors は背景グラデーション用の暗めの2色。accent は明るい強調色。
- mood は動画の雰囲気: danger（危険・注意・毒など）/ nature（動物・植物）/ water_space（海・空・宇宙）/
  food（食べ物）/ history（歴史・文化）/ science（科学・人体・技術）/ fun（その他の楽しい話）。
- sources は出典URLの配列。topic はテーマを短く。thumb_text / thumb_prompt はサムネイル用。

# 台本案
{draft}"""


def _text(msg):
    return "".join(b.text for b in msg.content if b.type == "text")


def research(client, prompt, max_uses=8, task="factcheck"):
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses}]
    for _ in range(5):
        msg = client.beta.messages.create(
            model=llm.model(task), max_tokens=16000, messages=messages, tools=tools,
            output_config={"effort": llm.effort(task)},
            cache_control={"type": "ephemeral"},  # search results re-read on later turns bill at the cache rate
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        llm.record(task, msg)
        print(f"  [{task}] turn: in={msg.usage.input_tokens} cache_read={msg.usage.cache_read_input_tokens or 0} "
              f"out={msg.usage.output_tokens} stop={msg.stop_reason}")
        if msg.stop_reason == "refusal":
            raise RuntimeError(f"refused: {msg.stop_details}")
        if msg.stop_reason != "pause_turn":
            return _text(msg)
        messages = messages[:1] + [{"role": "assistant", "content": msg.content}]
    raise RuntimeError("research did not finish")


def to_script(client, draft):
    res = client.messages.parse(
        model=llm.model("convert"), max_tokens=16000, output_format=Script,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": FORMAT_PROMPT.format(draft=draft)}],
    )
    llm.record("convert", res)
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
        return {"type": "compare", "relation": v.relation, "items": [
            {k: getattr(i, k) for k in ("label", "value", "color", "note", "emoji") if getattr(i, k)}
            for i in v.items[:2]]}
    if t == "list":
        return {"type": "list", "items": [{"emoji": i.emoji, "text": i.text} for i in v.items[:4]]}
    if t == "pie":
        return {"type": "pie", "center": v.center,
                "segments": [{"label": s.label, "value": s.value, "color": s.color} for s in v.segments]}
    if t in ("quiz", "quiz_answer"):
        return {"type": "quiz", "question": v.text, "choices": [i.text for i in v.items[:3]],
                "answer": int(v.number or 1), "reveal": t == "quiz_answer", "countdown": t == "quiz"}
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


CTA_RE = re.compile(r"[^。！？!?]*(コメント|フォロー|チャンネル登録)[^。！？!?]*[。！？!?]?")


def clean_narration(text, comment_prompt=""):
    """Drop CTA sentences (they belong to the outro) and any trailing unfinished fragment."""
    t = text
    if comment_prompt:
        t = t.replace(comment_prompt, "")
    t = CTA_RE.sub("", t).strip()
    m = re.search(r"^(.*[。！？!?」])", t, re.S)
    if m and m.group(1) != t:  # trailing fragment such as "だから憧れの、"
        t = m.group(1)
    t = t.strip().lstrip("。！？!?、 ")
    return t if re.search(r"[^\s。！？!?、」「]", t) else ""


def _scene(sc, comment_prompt=""):
    v = _visual(sc.visual)
    out = {"narration": clean_narration(sc.narration, comment_prompt), "visual": v}
    if v.get("type") == "quiz" and v.get("countdown"):
        out["hold"] = 3.3  # room for the 3-2-1 countdown
    return out


def outro_scene(outro, comment_prompt):
    """Last scene: the spoken comment prompt is also what the screen shows; follow line underneath."""
    cp = (comment_prompt or outro.get("narration", "")).strip()
    vis = {"type": "text", "emoji": "💬", "text": cp, "size": 96, "sub": outro.get("sub", "▶ フォローで毎日雑学")}
    return dict({k: v for k, v in outro.items() if k != "sub"}, narration=cp, visual=vis, no_subtitle=True)


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
        "mood": s.mood,
        "pronunciations": {p.word: p.reading for p in s.pronunciations},
        "scenes": [x for x in (_scene(sc, s.comment_prompt) for sc in s.scenes) if x["narration"]]
                  + [outro_scene(outro, s.comment_prompt)],
        "comment_prompt": s.comment_prompt,
        "sources": s.sources,
        "thumb_text": s.thumb_text,
        "thumb_prompt": s.thumb_prompt,
    }


def categories(genre=None):
    return list((genre or {}).get("categories") or CATEGORIES)


def genre_guide(genre=None):
    """Extra rules for a genre profile (e.g. adult trivia): tone, taboos, image rules."""
    g = genre or {}
    parts = []
    if g.get("guide"):
        parts.append("# このチャンネルのジャンル\n" + g["guide"].strip())
    if g.get("image_rules"):
        parts.append("# 写真・AI画像・サムネイルの決まり（必ず守る）\n" + g["image_rules"].strip())
    return ("\n\n" + "\n\n".join(parts)) if parts else ""


def pick_category(seed=None, recent_categories=(), weights=None, genre=None):
    rng = random.Random(seed or dt.date.today().toordinal())
    cats = categories(genre)
    pool = [c for c in cats if c not in set(recent_categories)] or cats
    w = [(weights or {}).get(c, 1.0) for c in pool]
    return rng.choices(pool, weights=w)[0]


def generate(history, outro, seed=None, recent_categories=(), writer="claude", category=None, topic=None,
             fmt=None, format_weights=None, category_weights=None, genre=None):
    """Write a script. With topic set, write about that topic instead of choosing one.
    genre (profile config "genre") swaps the category list and adds tone/safety rules."""
    llm.reset()
    genre = genre or {}
    category = category or pick_category(seed, recent_categories, category_weights, genre)
    print(f"[generate:{writer}] category: {category}" + (f" / topic: {topic}" if topic else ""))
    rng = random.Random((seed or dt.date.today().toordinal()) * 7 + 3)
    fw = format_weights or FORMAT_WEIGHTS
    fw = {k: w for k, w in fw.items() if k in FORMAT_WEIGHTS}  # drop retired formats (e.g. quiz)
    fmt = fmt if fmt in FORMAT_WEIGHTS else rng.choices(list(fw), weights=list(fw.values()))[0]
    print(f"[generate] format: {fmt}")
    prompt = RESEARCH_PROMPT.format(theme_line=theme_line(category, history, topic),
                                    channel_kind=genre.get("channel_kind", "雑学チャンネル"),
                                    genre_guide=genre_guide(genre),
                                    format_name=FORMATS[fmt][0], format_guide=FORMATS[fmt][1])
    hashtag = genre.get("hashtag")
    if hashtag:  # e.g. "#大人の雑学" instead of "#雑学"
        prompt = prompt.replace("末尾に #雑学 #shorts", f"末尾に {hashtag} #shorts")
    notes = None
    if writer in ("gemini", "gemini+claude"):
        from . import gemini
        s = gemini.write_script(prompt, FORMAT_PROMPT, Script)
        if writer == "gemini+claude":
            from .factcheck import check
            print("[generate] Claude fact-check...")
            s, notes = check(s, extra=genre_guide(genre))
            for n in notes:
                print(f"  [factcheck] {n}")
    else:
        client = anthropic.Anthropic()
        s = to_script(client, research(client, prompt))
    out = assemble(s, category, outro, writer)
    out["format"] = fmt
    out["gen_cost"] = llm.cost_summary()
    if notes is not None:
        out["factcheck"] = notes
    return out
