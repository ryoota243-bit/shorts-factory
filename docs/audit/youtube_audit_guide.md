# YouTube API 審査（監査）申請まとめ

「非公開」固定を外し、公開・予約公開まで自動化するための申請メモです。
2026年11月24〜25日のチャットの内容をまとめました。

---

## いまの状況

| 項目 | 状態 |
|---|---|
| ホームページ・プライバシーポリシー・利用規約 | ✅ 公開済み（https://ryoota243-bit.github.io/） |
| OAuth 同意画面 | ✅ 本番環境に公開済み |
| アーキテクチャ図・ユーザーフロー図 | ✅ 作成済み（このフォルダの PNG） |
| 利用規約の PDF | ✅ 作成済み（`terms-of-service.pdf`） |
| YouTube 連携の設定（手順3〜5） | ⬜ 未完了 → `docs/youtube_setup.md` |
| ホームページに YouTube チャンネルへのリンクを追加 | ⬜ チャンネルURLを Claude に伝える |
| ホームページ・プライバシーポリシーのスクリーンショット | ⬜ 上の追加後に撮影 |
| 画面録画（OAuth の流れ・アップロード・YouTube Studio） | ⬜ 連携設定の完了後に撮影 |
| 審査フォームの提出 | ⬜ 資料がそろってから一気に入力して提出 |

**フォームは途中保存できないことが多いので、資料をすべてそろえてから最初から入力してください。**

---

## 残りの作業（この順番で）

1. **YouTube 連携の設定を完了する**（`docs/youtube_setup.md` の手順3〜5）
   - OAuth クライアント ID（ウェブ アプリケーション、リダイレクト URI は `https://developers.google.com/oauthplayground`）
   - OAuth Playground でリフレッシュトークンを取得
   - GitHub Secrets に `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` / `YOUTUBE_REFRESH_TOKEN` を登録
2. **Claude に YouTube チャンネルの URL を伝える** → ホームページに「YouTube のブランディング」（チャンネルへのリンク）を追加してもらう
3. **スクリーンショットを撮る**（ブラウザのアドレスバーを含めること、1280×720以上）
   - `https://ryoota243-bit.github.io/` → `homepage.png`
   - `https://ryoota243-bit.github.io/privacy.html` → `privacy-policy.png`
     （「YouTube 利用規約」「Google プライバシーポリシー」のリンクと「データの保存期間と削除」が写るように）
4. **Claude にテストアップロードを頼む** → 動作確認
5. **画面録画を撮る**（Claude に台本を作ってもらう）
   - Google の認証画面で許可する場面（同意画面・スコープ・取り消し方法）
   - GitHub Actions でツールを実行し、ログでアップロード成功が分かる場面
   - YouTube Studio で、アップロードされた動画（タイトル・説明・サムネイル）を確認する場面
   - YouTube に「限定公開」で上げ、リンクを資料に載せる
6. **審査フォームに入力して提出**：https://support.google.com/youtube/contact/yt_api_form

---

## 審査フォームの回答

### 組織・ウェブサイト
| 項目 | 回答 |
|---|---|
| 組織名 | チャンネル名「思わず話したくなる雑学」またはお名前 |
| 組織のメインウェブサイト | `https://ryoota243-bit.github.io/` |

### YouTube に関連する、組織の業務の説明（英語で貼り付け）
```
I am an individual creator who runs the Japanese YouTube channel "思わず話したくなる雑学" (Trivia You'll Want to Share). The channel publishes one short educational video (YouTube Shorts, about 40 seconds) every day, covering trivia about animals, space, the human body, food, history and everyday objects. Every script is fact-checked against reliable sources (public institutions, universities, encyclopedias and specialist media), and the main sources are listed in each video's description.

I produce the videos with my own private tool, "shorts-factory". It assembles narration, subtitles, diagrams and images into a video file. I use the YouTube Data API only to upload these videos to my own channel (videos.insert) and to set their custom thumbnails (thumbnails.set), using OAuth with my own Google account. The tool is not offered to anyone else, has no other users, and does not access, collect or store any data belonging to other YouTube users or channels.

I am requesting the audit so that uploads are no longer locked to private, allowing me to publish or schedule my daily videos directly. I review each video before it goes public. Website and privacy policy: https://ryoota243-bit.github.io/
```
※ 最後の「I review each video before it goes public.」（公開前に確認している）は、完全自動で公開する運用なら削除してください。

### API クライアントの詳細
| 項目 | 回答 |
|---|---|
| API クライアント名 | `shorts-factory`（OAuth 同意画面のアプリ名と同じに） |
| 名前に「YouTube」を含むか | いいえ |
| メインのアクセス URL | `https://ryoota243-bit.github.io/` |
| プライバシー ポリシーの URL | `https://ryoota243-bit.github.io/privacy.html` |
| 利用規約の URL | `https://ryoota243-bit.github.io/terms.html` |
| 一般公開されているか | いいえ（利用者は運営者本人のみ。非公開の GitHub リポジトリ上で自動実行） |

### デモアカウントの認証情報
**自分の Google アカウントのパスワードは絶対に入力しない。**

| 項目 | 回答 |
|---|---|
| ユーザー名 / パスワード / ログイン URL | 空欄 |
| アクセスに関する特記事項 | 下の英文 |
| 同意のチェックボックス | チェックする |

```
No demo account is available. shorts-factory is an internal tool with no login screen or user interface; it runs as a scheduled job in the channel owner's private GitHub repository and is used only by the channel owner. An end-to-end screencast of the OAuth authorization and the upload flow is provided in Section 6.
```

### プロジェクト
| 項目 | 回答 |
|---|---|
| 追加するプロジェクト番号の数 | 1 |
| Google Cloud プロジェクト番号 | Cloud コンソール →「ダッシュボード」の「プロジェクト番号」（数字のみ。プロジェクト ID ではない） |
| ユースケースのカテゴリ | ✅ 動画のアップロードとアカウント管理 ／ ✅ 社内ツール（「教育と研究」は選ばない） |
| OAuth 2.0 でログインする必要があるか | はい |
| API の想定使用量 | 一番小さい区分（1日3本でも約5,000ユニットで、既定の10,000以内） |

### セクション6：証拠資料（各欄1ファイル、10MB未満）
| 欄 | ファイル |
|---|---|
| アーキテクチャ図 | `architecture-diagram.png` |
| ユーザーフロー図 | `user-flow-diagram.png` |
| プライバシーポリシーのスクリーンショット | `privacy-policy.png`（自分で撮影） |
| ホームページのスクリーンショット | `homepage.png`（自分で撮影） |
| 利用規約のドキュメント | `terms-of-service.pdf` |
| 条件付きの証拠資料（OAuth の流れ・アップロード画面・ダッシュボード） | 画面録画のリンクとスクリーンショットをまとめた PDF（Claude と作成） |
| その他の補足資料 | 必要に応じて同上 |

---

## 審査に通ったあと
- `config.json` の `youtube.privacy` を `"public"` にする（予約公開なら `publish_at` を設定）
- 審査は数週間かかることがあり、通るかどうかは Google の判断次第
- 審査中も「非公開で自動アップロード → YouTube Studio で公開に切り替え」の運用は続けられる
