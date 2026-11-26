# YouTube 自動アップロードの設定手順（最初の1回だけ・約15分）

PC のブラウザで行ってください。画面の文言は Google 側の変更で多少違うことがあります。
途中で分からなくなったら、どの画面で止まったかを Claude に伝えてください。

最終的に GitHub に登録する値は次の3つです。

| Secret 名 | どこで手に入るか |
|---|---|
| `YOUTUBE_CLIENT_ID` | 手順3 |
| `YOUTUBE_CLIENT_SECRET` | 手順3 |
| `YOUTUBE_REFRESH_TOKEN` | 手順4 |

---

## 手順1：YouTube Data API v3 を有効にする

1. https://console.cloud.google.com を開く（Gemini のキーを作ったのと同じ Google アカウント）
2. 画面上部のプロジェクト選択で、Gemini のキーと同じプロジェクトを選ぶ（新しく作ってもよい）
3. 左メニュー「API とサービス」→「ライブラリ」
4. 「YouTube Data API v3」を検索して開き、「有効にする」

## 手順2：OAuth 同意画面を作り、「本番」にする

1. 左メニュー「API とサービス」→「OAuth 同意画面」（「Google Auth Platform」と表示されることもあります）
2. 「開始」または「作成」を押し、次のように入力
   - アプリ名：`shorts-factory`（何でもよい）
   - ユーザーサポートメール：自分のメールアドレス
   - 対象（ユーザーの種類）：**外部**
   - 連絡先メール：自分のメールアドレス
3. 作成後、「対象」（Audience）の画面で **「アプリを公開」→「本番環境にプッシュ」** を押す
   - ⚠️ **ここが重要**：「テスト」のままだと、7日ごとに認証が切れてアップロードが止まります
   - 「確認が必要です」と出ても、自分だけで使う分にはそのまま進めて問題ありません

## 手順3：OAuth クライアント ID を作る

1. 左メニュー「API とサービス」→「認証情報」（または「クライアント」）
2. 「認証情報を作成」→「OAuth クライアント ID」
3. アプリケーションの種類：**ウェブ アプリケーション**
4. 名前：`shorts-factory`
5. 「承認済みのリダイレクト URI」に次をそのまま追加
   ```
   https://developers.google.com/oauthplayground
   ```
6. 「作成」→ 表示された **クライアント ID** と **クライアント シークレット** をメモ帳にコピー

## 手順4：リフレッシュトークンを取得する（OAuth Playground）

1. https://developers.google.com/oauthplayground を開く
2. 右上の **歯車アイコン** を押し、**「Use your own OAuth credentials」にチェック**
   - 「OAuth Client ID」「OAuth Client secret」に手順3の値を貼り付けて閉じる
3. 左の「Step 1」の一番下の入力欄（Input your own scopes）に次を貼り付ける
   ```
   https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube
   ```
4. **「Authorize APIs」** を押す
5. Google のログイン画面で、**動画を投稿するチャンネルのアカウント**を選ぶ
   - チャンネルが「ブランドアカウント」の場合は、そのチャンネル名を選んでください
   - 「このアプリは Google で確認されていません」と出たら、「詳細」→「shorts-factory（安全ではないページ）に移動」→「続行」
6. Playground に戻ったら「Step 2」で **「Exchange authorization code for tokens」** を押す
7. 表示された **Refresh token** をコピー（`1//` で始まる長い文字列）

## 手順5：GitHub に登録する

https://github.com/ryoota243-bit/shorts-factory/settings/secrets/actions →「New repository secret」で、
3つの値をそれぞれ登録します。

| Name | Secret |
|---|---|
| `YOUTUBE_CLIENT_ID` | 手順3のクライアント ID |
| `YOUTUBE_CLIENT_SECRET` | 手順3のクライアント シークレット |
| `YOUTUBE_REFRESH_TOKEN` | 手順4のリフレッシュトークン |

**キーやトークンはチャットに貼らないでください。**

## 手順6（任意・推奨）：カスタムサムネイルを使えるようにする

https://www.youtube.com/verify で電話番号の確認をしておくと、サムネイル画像の自動設定が有効になります
（ショートでは反映されない場合もあります。その場合も動画の最初のコマがサムネイル画面になっています）。

---

## 設定後の動き

- 毎朝 5:17 の自動生成のあと、動画が **非公開** で自動アップロードされます
- Releases のノートと毎朝の報告に、YouTube の URL が載ります
- YouTube Studio で内容を確認し、「公開」に切り替えてください

## 「公開」まで完全に自動化したい場合（審査）

Google の審査（YouTube API Services の監査）に通ると、非公開の固定が外れ、公開や予約公開を自動で行えます。

- 申請フォーム：https://support.google.com/youtube/contact/yt_api_form
- 通ったら `config.json` の `youtube.privacy` を `"public"` に（予約公開なら `publish_at` を設定）

## 停止したいとき

`config.json` の `youtube.enabled` を `false` にするか、GitHub の Secrets から `YOUTUBE_REFRESH_TOKEN` を削除します。
アップロードを止めても、動画の生成と Releases への保存は続きます。

---

## 一言トリビアチャンネル（2つ目のチャンネル）を設定する

手順1〜3（API・同意画面・クライアント ID）は雑学チャンネルのものをそのまま使えます。

1. 手順4と同じように OAuth Playground でリフレッシュトークンを取得する。
   - 手順4-5 のログイン画面で、**一言トリビアのチャンネル**（ブランドアカウントならそのチャンネル名）を選ぶ
2. GitHub の Secrets に次の名前で登録する

| Name | Secret |
|---|---|
| `TRIVIA_YOUTUBE_REFRESH_TOKEN` | 上で取得した、トリビアチャンネルのリフレッシュトークン |

- 別の Google Cloud プロジェクトのクライアントを使う場合だけ、`TRIVIA_YOUTUBE_CLIENT_ID` / `TRIVIA_YOUTUBE_CLIENT_SECRET` も登録します
  （未登録なら雑学チャンネルと同じクライアントを使います）。
- `TRIVIA_YOUTUBE_REFRESH_TOKEN` が未登録の間、トリビア動画はアップロードされません（雑学チャンネルのトークンは使いません）。
