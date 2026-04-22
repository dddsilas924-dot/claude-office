# Discord Bot V2 — 要件定義書

> 生成日: 2026-04-22
> 作成者: 司令塔（4.6）→ 実装: 4.7
> 対象ファイル: scripts/discord_bot.py (984行)

---

## 1. 概要

Discord Bot「Commander Bridge」を「伝書鳩」にリネームし、以下の機能を追加・修正する。

## 2. 必須タスク（Phase 1 — 即実装）

### TASK-01: Bot名変更「伝書鳩」
- docstring, ログ出力, embed の "Commander Bridge" を全て「伝書鳩」に置換
- `set_footer(text="Claude Office × Commander Bridge")` → `"ミスターDオフィス × 伝書鳩"`
- embed title の "Commander Bridge" も全置換
- discord_bot.py 内の変数名 `CommanderBridgeBot` はコード内部なのでそのままでOK

### TASK-02: サーバー名変更 API
- `on_ready` 内で guild.edit(name="ミスターDオフィス") を呼ぶ
- 既に正しい名前なら何もしない（冪等）
- MANAGE_GUILD 権限が必要。権限不足時はログ出力して続行

### TASK-03: インタラクション失敗修正（14部門中継パネル）
- 現状: relay panel のボタンが押されても discord_bot.py にハンドラーがない → 3秒タイムアウト
- 修正: `discord.ui.View` + `discord.ui.Button` で14部門ボタンの永続View を実装
- ボタン押下 → interaction.response.send_modal() でメッセージ入力モーダル表示
- モーダル送信 → 該当部門チャンネルにメッセージ転送 + Canvas に HMAC イベント送信
- `bot.add_view(RelayPanelView())` で永続View登録（Bot再起動後も動作）
- custom_id は `relay_{dept_id}` 形式

### TASK-04: ファイル・画像読み込み対応
- on_message で添付ファイル (message.attachments) を検出
- 対応フォーマット: 画像(png/jpg/gif/webp), PDF, MD, txt, 動画(mp4/mov)
- テキスト系(MD/txt): 内容を読み込んで Canvas イベントに含める（最大2000文字）
- 画像系: URL を Canvas イベントの metadata に含める
- PDF: ファイル名とサイズを Canvas イベントに含める
- 動画: ファイル名・サイズ・URL を metadata に含める
- /send コマンド: メッセージ + 添付ファイルをまとめて宛先部門に送信

### TASK-05: 保管庫チャンネル新設
- on_ready で以下のチャンネルが存在しなければ自動作成:
  - `📦ドラの成果物` — えむが指示して作らせた成果物
  - `📦部門の成果物` — 部門が自律的に作った成果物
- カテゴリ: 既存の適切なカテゴリに入れる（なければ「保管庫」カテゴリ作成）
- CHANNELS dict にも動的追加

### TASK-06: 応答ディレイ改善
- send_canvas_event の urllib.request を aiohttp に置換（ノンブロッキング化）
- aiohttp.ClientSession をBot起動時に作成、close時に閉じる
- 依存追加: `pip install aiohttp`
- ハートビートも aiohttp 経由に切替

## 3. 設計ルール（discord_bot.py 冒頭コメントに記載）

以下を docstring に追記:

```
アーキテクチャルール:
- Discord = 部門が自律的に動く場。えむは審査員
- Office (Canvas) = 視覚的に見える場
- Claude Code = えむが直接指示する場（別世界線）

指揮系統:
- 基本: えむ → フィル（司令塔）→ 伝書鳩 → 各部門
- 例外: 外部イレギュラーは直接部門に届く
- 14部門パネル: えむ（どらどら）専用の指示ツール

成果物:
- えむ指示の成果物 → 📦ドラの成果物
- 部門自律の成果物 → 📦部門の成果物

セッション連携:
- Claude Code セッション切替時 → Discord に通知
- 全ログは流さない → 要約・状態のみ同期
```

## 4. 技術仕様

### 依存関係
- discord.py >= 2.0 (既存)
- aiohttp (新規追加)
- python-dotenv (既存)

### 環境変数 (.env)
- DISCORD_BOT_TOKEN (既存)
- EXTERNAL_EVENT_SECRET (既存)
- DISCORD_SERVER_ID (既存 GUILD_ID に統合してもよい)

### ファイル構成
- scripts/discord_bot.py — メインBot（全修正ここ）

## 5. テスト項目

- [ ] Bot起動後サーバー名が「ミスターDオフィス」に変更される
- [ ] 14部門パネルのボタン押下でモーダルが開く
- [ ] モーダルからメッセージ送信で部門チャンネルに届く
- [ ] 画像添付メッセージ → Canvas イベントに metadata.image_url が含まれる
- [ ] PDF添付 → ファイル名・サイズが Canvas イベントに含まれる
- [ ] 保管庫チャンネルが自動作成される
- [ ] send_canvas_event が aiohttp で非同期動作する
- [ ] embed の全テキストに "Commander Bridge" が含まれない（伝書鳩に統一）
- [ ] Bot再起動後もボタンインタラクションが動作する（永続View）
