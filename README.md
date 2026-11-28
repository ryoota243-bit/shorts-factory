# Shorts Factory — 雑学ショート自動生成

毎朝 5:17（日本時間）に GitHub Actions が自動で動き、雑学ショート動画（縦型 1080×1920）を作ります。

1. Gemini が Google 検索で調べて雑学の台本を書き、Claude が Web 検索で事実を校閲・修正する（過去のテーマとは重複しない）
2. VOICEVOX（ずんだもん）でナレーションを作る
3. 写真・動画を Pexels / Pixabay から探し、見つからなければ Gemini で AI 画像を作り、それも無理なら図解にする
4. 字幕・図解・BGM・効果音を合成して MP4 にする
5. **Releases** に動画とコピペ用のタイトル・概要欄・タグを置く
6. YouTube に **非公開** で自動アップロード（設定手順: `docs/youtube_setup.md`）→ YouTube Studio で公開に切り替え

## 毎日の使い方

リポジトリの **Releases** を開き、その日の mp4 をダウンロードします。
ノートにあるタイトル・概要欄をコピーして、YouTube アプリから投稿してください。

- **今すぐ作る**：Actions → `daily-shorts` → `Run workflow`（本数も指定できます）
- **台本AIの比較**：`Run workflow` で「比較モード」にチェックすると、同じお題を Claude と Gemini の両方で台本化して2本ずつ作ります
- **台本AIの切り替え**：`config.json` の `script_writer` を `gemini+claude`（標準：Gemini が執筆→Claude が校閲）/ `claude` / `gemini` に
- **自動チェック**：毎回、Claude が完成動画のコマと台本を見て問題点を洗い出し、Releases のノートに載せます
- **サムネイル**：毎回 `〜.thumb.png`（AI画像＋キャッチコピー）を Releases に置きます。動画の最初の0.5秒も同じ画面なので、アプリの「カバー」で選べます
- **読み間違い対策**：VOICEVOX の実際の読みを Claude が確認し、誤読は `dictionary.json`（辞書）に登録します。手で追記もできます
- **自分で書いた台本を優先する**：`scripts/queue/` に JSON を置くと、次の回で使われます
- **使い終わった台本**：`scripts/archive/` に、作成済みテーマは `history.json` に残ります

## 一言トリビアチャンネル（別チャンネル）

雑学チャンネルとは別の YouTube チャンネル（仮名「ちょっと大人な雑学」）向けに、性・恋愛・体・動物の求愛にまつわる
一言雑学を5〜6個、30〜40秒で紹介する動画を作ります。

テーマは `channels/trivia/config.json` の `categories`（お題の候補）と `content_rules`（守ること）で決まり、書き換えれば別のテーマにできます。
YouTube の規約（性的なコンテンツは年齢制限・削除の対象）に合わせて、次は台本AI・校閲AI・画像AIのすべてに守らせています。

- 科学・歴史・文化として伝える。露骨な言葉や性行為の描写はしない（匂わせ程度まで）
- 未成年に関わる話題・実在の人物の性的な話・性暴力・偏見は扱わない。健康の話は医学的に確認できる事実だけ
- 裸・下着・水着・性的なポーズは、写真・AI画像・絵文字のどれでも出さない（人物は服を着た大人の日常の場面だけ）
**方針が決まるまで自動実行は止めています**（Actions → `trivia-shorts` → `Run workflow` で手動実行のみ。
毎日自動にするときは `.github/workflows/trivia-daily.yml` の `schedule` のコメントを外す）。

雑学チャンネルと見分けがつくよう、見た目・構成・音を変えています（`factory/cardstyle.py`）。

| | 雑学チャンネル | 一言トリビアチャンネル |
|---|---|---|
| 画面 | 暗いグラデーション＋下に字幕 | トリビアごとに色が変わる明るいカード。写真は白フチの傾いたフレーム、文は画面の主役として大きく表示（字幕なし） |
| 切り替え | クロスフェード | カードが横にスワイプ |
| 構成 | 驚きの一言→説明→冒頭に戻るループ | 掴みの一言→番号付きトリビア5個（01, 02…と上部の進行バー）→「いくつ知ってた？」と知っていた数の目安。各トリビアは「〇〇は、」→約2秒の溜め（「？」カードとチクタク音）→答え（カードがめくれて写真と「ピンポン」）。馴染みのない横文字は直後に「ちなみに、」の補足カード |
| 声・音楽 | ずんだもん・落ち着いた曲 | 春日部つむぎ・テンポの速い軽い曲・スワイプ音 |

- 設定：`channels/trivia/config.json`（チャンネル名・概要欄の定型文・声・締めの目安の文言など。書いた項目だけ `config.json` を上書き）
- BGM：`channels/trivia/bgm/` に mp3 を置くとそれを使用（無ければ自動作曲）
- 台本・履歴・分析：`channels/trivia/` 以下に、雑学チャンネルとは別に保存（動画名は `trivia_日付_番号`、リリースのタグは `trivia-v…`）
- YouTube：Secret `TRIVIA_YOUTUBE_REFRESH_TOKEN` にトリビアチャンネルのトークンを登録するとアップロードされます
  （手順は `docs/youtube_setup.md` の最後）。未登録の間はアップロードせず、雑学チャンネルに誤って上がることもありません
- 個数・溜めの秒数は `channels/trivia/config.json` の `rapid`（`facts`, `tease_pause`）。`tease_pause` を消すと溜めなしの構成に戻ります
- 手元では `python daily.py --channel trivia`

## 初期設定：APIキー（Settings → Secrets and variables → Actions → New repository secret）

| Secret 名 | 用途 | 取得先 | 必須 |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | 台本の自動作成 | https://console.anthropic.com/ （支払い方法の登録が必要） | ✅ |
| `PEXELS_API_KEY` | 写真・動画素材（無料） | https://www.pexels.com/api/ | 推奨 |
| `PIXABAY_API_KEY` | 写真・動画素材（無料） | https://pixabay.com/api/docs/ （ログイン後に表示） | 推奨 |
| `GEMINI_API_KEY` | AI 画像生成・Gemini 台本 | https://aistudio.google.com/apikey | 推奨 |
| `GOOGLE_TTS_API_KEY` | Google の声を使う場合のみ | Google Cloud コンソール | 任意 |

キーが無い機能は自動で飛ばします（例：素材のキーが無ければ図解で代用）。

## 設定（config.json）

- `tts.backend`：`voicevox`（標準）/ `google` / `openjtalk`
- `tts.voicevox.speaker`：声の番号（3=ずんだもん、2=四国めたん、8=春日部つむぎ など）。変えたら `credit` も合わせて変更
- `daily.count`：1日に作る本数
- `outro`：最後に必ず付くフォロー呼びかけの場面
- `bgm/` に mp3 を置くと、BGM をランダムに使用（無ければ自動作曲）

VOICEVOX を使った動画は、概要欄にクレジット（例「VOICEVOX:ずんだもん」）が自動で入ります。
規約に必要な表記なので消さないでください。

## 手元で動かす場合

```bash
pip install -r requirements.txt      # ffmpeg, Noto CJK / Noto Color Emoji フォントも必要
python make.py scripts/queue/001_octopus.json --tts openjtalk
python daily.py                       # 毎日の処理を1回実行
```

## 台本 JSON の書き方

```jsonc
{
  "title": "画面上部の見出し（[ ]で強調）",
  "youtube_title": "投稿タイトル #shorts", "description": "概要欄", "tags": ["雑学"],
  "theme": { "bg": ["#0f2027", "#2c5364"], "accent": "#ffd600" },
  "pronunciations": { "3分の2": "さんぶんのに" },
  "scenes": [ { "narration": "読み上げ文（[ ]は字幕で強調）", "visual": { "type": "number", "value": 3, "unit": "つ", "label": "心臓の数" } } ]
}
```

visual の種類：`photo` / `video`（query, ai_prompt, fallback）、`ai`、`illustration`（emoji, text）、`number`（value, unit, label）、
`compare`（items: label, value, color, note）、`list`（items: emoji, text）、`pie`（segments: label, value, color / center）、`text`（text, emoji）。
