"""Claude fact-checks a script written by another model (plan C: Gemini writes, Claude checks)."""
import json
import re

import anthropic

from .generate import research, to_script

CHECK_PROMPT = """あなたは日本語の雑学ショート動画の校閲担当です。別のAIが書いた次の台本（JSON）の事実関係を、
web検索で信頼できる情報源（公的機関・大学・百科事典・専門メディア・企業の公式情報）にあたって確認してください。

# 確認すること
- ナレーションと画面の文字（title, text, label, value, note, items, segments, center, number, unit）の
  主張・数字・単位・固有名詞・年・順序
- 言い過ぎや誤解を招く表現（例: 任意の規格を「義務」、「世界初」「必ず」「唯一」の根拠、因果関係の誇張、
  諸説あるものの断定）
- 1シーンが長すぎないか（35文字以内）、絵文字だけ・文字だけの場面が続いていないか（続くなら分けるか映像を変える）
- number の label が単独で意味の通る見出しか（「土星の体積は地球の約」のような文の途中は不可。例「地球と比べた体積」）
- compare の2つの value の単位がそろっているか、illustration/text の文字がナレーションの丸写しになっていないか
- 同じ種類の映像が2シーン続いていないか（続く場合は1つにまとめるか種類を変える）、emoji が内容を正しく表しているか
- compare の relation が2つの関係に合っているか（対決・対比なら vs、変化なら arrow、同じ量なら equal、
  内訳なら plus、誤解と真実なら not_equal、単に並べるだけなら none。対立がないのに vs になっていれば直す）
- 図解の使い方が内容と合っているか（pie は全体に対する割合のときだけ。compare は違う2つの対比だけで、
  同じ量の言い換えには使わない。number のシーンはナレーションがその数字の話だけになっているか）
- sources が主張を裏付けているか
- サムネイルのキャッチコピー（thumb_text）が内容と食い違う誇張になっていないか
- 途中で「最後にもっと驚く事実が」などと予告している場合、最後で本当に回収しているか
- クイズ形式の場合、正解（number）が事実どおりか、不正解の選択肢が誤って正しくなっていないか
- 最後のシーンが単独で意味の通る締めの文になっているか（「だからこそ言えるのです」のような中身のない文は不可。
  冒頭の事実を言い直す文に直す）
- すべてのシーンのナレーションが完結した文で終わっているか（「、」で途切れていないか）、
  シーンのナレーションにコメントやフォローの呼びかけが混ざっていないか（混ざっていれば取り除く）
{format_note}
- 数字どうしの整合性（例: 昼＋夜の合計が1周の時間と合うか、ナレーションと画面の数字が一致するか）
- 写真・AI画像の指定（query / ai_prompt / thumb_prompt）が、紙幣・硬貨の図柄や外国の文字・国旗など
  別の国のものが写りやすい内容になっていないか（なっていれば一般的な情景に変える）
- 写真・AI画像の指定（query / ai_prompt）が、特定の機体・建物・場所の正確な見た目を必要としていないか
  （必要なら一般的な情景に変える）

# 必ず検索で一次情報を確かめるもの（節約より優先）
- 調査・統計の数字（〇%、〇人、〇年度の調査など）と、その調査名・年度。公的機関や調査元の発表で確認できなければ、
  数字を削るか「半数近く」のようなぼかした表現にする。記憶や二次情報だけで残さない。

# 検索の節約（費用を抑えるため。確認の厳しさは下げない）
- 台本の sources は、台本作成時に参照された出典です。まずそれで裏付けが取れる主張は、追加の検索をしなくてよい。
- 検索は、数字・年・固有名詞・「世界初」「唯一」などの言い切りで、sources だけでは確かめられないものに使う。
- 同じ主張を何度も検索しない。1回の検索で複数の主張をまとめて確認できるなら、まとめる。

# 手順
1. 主張ごとに確認し、誤り・不確かな点と修正案を挙げる。
2. 修正を反映した台本全体を書き直して示す。元の構成・シーン数・長さ・語り口（物語性やオチ）は
   できるだけ保つ（1シーン目の映像は photo か ai のまま。1シーン目の最初の一言は10文字前後で一番意外な事実を
   言い切る形を保ち、前置きを足さない）。数字・英字は半角。確認できない主張は削るか、断定しない表現にする（「公開前に要確認」のような保留は残さない）。
   画面の文字数の上限（compare の value 6文字・note 12文字、pie の center 6文字、list の text 14文字、
   1シーンのナレーション45文字）を守る。出典URLは確認に使ったものに更新する。
3. 最後に「## 校閲メモ」という見出しを付け、直した点を1行ずつ箇条書き（直す点がなければ「- 修正なし」）。

thumb_text は冒頭で読み上げられるタイトル。1シーン目がタイトルと同じ内容をくり返していないかも確認する。

# 台本
{script}"""

STORY_NOTE = """- 形式（通常・クイズ・誤解を正す）、ループの言い回し、comment_prompt（20文字以内の問いかけ）、
  ナレーション合計150〜220文字は保つ"""

RAPID_NOTE = """- これは「一言雑学の連発」形式（1シーン＝独立した雑学1つ、5〜6個）。雑学ごとに個別に事実を確認し、
  誤り・諸説ある・確認できない雑学は、確認できる別の雑学に差し替えるか削る（5個未満にはしない）。
  各雑学は1文・18〜32文字、ナレーション合計140〜180文字を保つ（長い補足を足さない）。
  1シーン目の映像を photo/ai にする条件、最初の一言を10文字前後にする条件、ループやオチは、この形式では不要。
  topic は「切り口: 雑学の要点／…」の形で、差し替えた内容に合わせて更新する。comment_prompt は20文字以内"""


def channel_note(rules):
    """The channel's content rules: the fact-checker must enforce them too (rewrite or drop what breaks them)."""
    if not rules:
        return ""
    from .generate import channel_block
    return ("\n- このチャンネルの決まり（守れていない雑学・映像の指定・タイトル・サムネイルは、守れる形に直すか"
            "別の雑学に差し替える）:\n" + channel_block(rules))


def check(script_model, fmt="standard", channel_rules=""):
    """Return (fixed Script, list of review notes)."""
    client = anthropic.Anthropic()
    payload = json.dumps(script_model.model_dump(), ensure_ascii=False, indent=1)
    report = research(client, CHECK_PROMPT.format(script=payload, format_note=(RAPID_NOTE if fmt == "rapid" else STORY_NOTE)
                                          + channel_note(channel_rules)), max_uses=10, task="factcheck")
    notes = []
    m = re.search(r"##\s*校閲メモ\s*\n(.*)", report, re.S)
    if m:
        notes = [l.strip(" -・*\t") for l in m.group(1).splitlines() if l.strip(" -・*\t")]
    fixed = to_script(client, report)
    return fixed, notes
