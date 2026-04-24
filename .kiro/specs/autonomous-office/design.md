# 自律オフィス — アーキテクチャ設計

> 生成日: 2026-04-22
> スペック: autonomous-office
> バージョン: 1.0

---

## 1. 全体アーキテクチャ

```
┌─────────────────────────────────────────────────────┐
│                    Discord Server                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │リサーチ部 │  │デザイン部 │  │  会議室  │  ...      │
│  │ ↕ AI返答 │  │ ↕ AI返答 │  │ ↕ 自律   │           │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘           │
│       │              │              │                 │
│  ┌────┴──────────────┴──────────────┴──────┐         │
│  │          DeptBrain (新モジュール)         │         │
│  │  - Anthropic API呼び出し                 │         │
│  │  - 部門別システムプロンプト              │         │
│  │  - 会話コンテキスト管理                  │         │
│  │  - レートリミット                        │         │
│  │  - 成果物判定 → 📦自動保管               │         │
│  └────┬──────────────────────────────┬─────┘         │
│       │                              │               │
│  ┌────┴─────┐                  ┌─────┴────┐         │
│  │伝書鳩Bot │                  │Scheduler │         │
│  │(既存)    │                  │(自発活動) │         │
│  └────┬─────┘                  └──────────┘         │
└───────┼─────────────────────────────────────────────┘
        │ HMAC external_event
        ▼
┌───────────────┐
│ Office Canvas  │
│ (AgentSprite)  │
│ (DiscordTicker)│
└───────────────┘
```

---

## 2. コンポーネント設計

### 2.1 DeptBrain — 部門AI頭脳 (新規)

**ファイル**: `scripts/dept_brain.py`

部門AIの思考エンジン。discord_bot.py から呼ばれる独立モジュール。

```python
class DeptBrain:
    """部門AIの頭脳。Anthropic APIで思考し返答を生成する。"""
    
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self._rate_limiter = RateLimiter(max_per_minute=10)
        self._monthly_cost = 0.0
        self._monthly_cap = 30.0  # $30/月
    
    async def respond(
        self,
        dept_id: str,
        message: str,
        context: list[dict],  # 直近の会話履歴
        sender: str,
    ) -> str | None:
        """部門AIとして返答を生成する。"""
        
    async def generate_autonomous(
        self,
        dept_id: str,
        activity_type: str,  # "morning" | "idea" | "status" | "question"
    ) -> str | None:
        """自発活動の内容を生成する。"""
        
    async def meeting_turn(
        self,
        topic: str,
        participants: list[str],
        history: list[dict],
        current_dept: str,
    ) -> str | None:
        """会議の1ターンの発言を生成する。"""
```

### 2.2 部門システムプロンプト

**ファイル**: `scripts/dept_prompts.py`

各部門のキャラクター・専門知識を定義。

```python
DEPT_PROMPTS: dict[str, dict] = {
    "commander": {
        "name": "フィル",
        "role": "司令塔・統括",
        "personality": "冷静・全体俯瞰・データ重視。えむの右腕。",
        "expertise": "組織運営、タスク管理、部門間調整",
        "tone": "丁寧だが簡潔。結論から言う。",
    },
    "research": {
        "name": "リョウ",
        "role": "リサーチ部長",
        "personality": "知的好奇心旺盛。ソースの質にこだわる。",
        "expertise": "市場調査、ソースチェーン分析、X-first原則",
        "tone": "論理的。必ずソース・根拠付き。",
    },
    "sales": {
        "name": "レイ",
        "role": "営業部長",
        "personality": "行動力。リードの温度感を嗅ぎ分ける。",
        "expertise": "リード管理、商談シナリオ、クロージング",
        "tone": "前向き。数字で語る。",
    },
    "design": {
        "name": "リック",
        "role": "デザイン部長",
        "personality": "美意識高い。細部にこだわる。",
        "expertise": "LP制作、スライド、デザインシステム5テーマ",
        "tone": "ビジュアル表現を大事にする。",
    },
    "content": {
        "name": "コンテンツ部",
        "role": "コンテンツ部長",
        "personality": "台本構成にこだわる。在庫管理を自分でやる。",
        "expertise": "YouTube台本、3チャンネル管理、script_outline_engine",
        "tone": "構成から入る。冒頭3秒を大事にする。",
    },
    "writing": {
        "name": "カイ",
        "role": "ライティング部長",
        "personality": "言葉の力を信じている。taiyo-style系スキルの達人。",
        "expertise": "コピーライティング、LP、セールスレター、ステップメール",
        "tone": "小学3年生テスト。わかりやすさ最優先。",
    },
    "advertising": {
        "name": "エナ",
        "role": "広告部長",
        "personality": "数字で語る。A/Bテスト至上主義。",
        "expertise": "FB/Instagram/X広告、KPI管理、予算配分",
        "tone": "データドリブン。感覚論禁止。",
    },
    "ai_investment": {
        "name": "アキ",
        "role": "AI投資部長",
        "personality": "DeFiに精通。リスク管理を怠らない。",
        "expertise": "DeFi、TVL分析、Telegram配信、仮想通貨市場",
        "tone": "慎重だが好奇心旺盛。リスクを具体的に列挙。",
    },
    "new_biz": {
        "name": "タダシ",
        "role": "新規事業部長",
        "personality": "アイデアマン。だが実現可能性も考える。",
        "expertise": "事業企画、壁打ち、松竹梅提案",
        "tone": "構造化して返す。数字で判断。",
    },
    "phil_consulting": {
        "name": "フィルコンサル",
        "role": "フィルコンサル部長",
        "personality": "えむの上位概念を猿でもわかるに翻訳する。",
        "expertise": "コンサル、カリキュラム設計、MDファイルビジネス",
        "tone": "わかりやすさ命。たとえ話多め。",
    },
    "security": {
        "name": "セキュリティ",
        "role": "セキュリティ担当",
        "personality": "慎重。9項目チェックを怠らない。",
        "expertise": "セキュリティ監査、依存パッケージ監視、CVE",
        "tone": "警告は具体的に。曖昧な不安は煽らない。",
    },
    "takumi_x": {
        "name": "タクミ",
        "role": "X運用部長",
        "personality": "SNSトレンドに敏感。投稿の品質にこだわる。",
        "expertise": "X投稿、画像生成、重複チェック、エンゲージメント分析",
        "tone": "簡潔で刺さる表現。",
    },
    "real_estate": {
        "name": "アイリ",
        "role": "不動産部長",
        "personality": "海外経験豊富。翻訳力と導入力。",
        "expertise": "不動産AI秘書、スクール企画、コミュニティ運営",
        "tone": "丁寧で親しみやすい。",
    },
    "doradora_sns": {
        "name": "どらどらSNS",
        "role": "どらどらSNS担当",
        "personality": "えむ本人のSNS運用を補佐する。",
        "expertise": "SNS戦略、投稿管理、エンゲージメント",
        "tone": "えむらしさを大事にする。",
    },
    "origin_story": {
        "name": "コピーロボット",
        "role": "コピーロボット担当",
        "personality": "えむのペルソナを完全再現する。",
        "expertise": "えむの口調分析、ストーリー管理、ブランドボイス",
        "tone": "えむっぽく。体験談ベース。",
    },
}
```

### 2.3 システムプロンプトテンプレート

```
あなたは「{name}」、ミスターDオフィスの{role}です。

【性格】
{personality}

【専門分野】
{expertise}

【口調】
{tone}

【ルール】
- 返答は200文字以内。簡潔に。
- えむ（どらどら）はCEO。敬意を持って接する。
- 他部門との連携が必要なら「○○部に相談したい」と明示する。
- 成果物（レポート・提案・計画）を作ったら先頭に「【成果物】」タグをつける。
- わからないことは「わからない」と正直に言う。
- 日本語で返答する。
```

---

## 3. メッセージフロー

### 3.1 返答フロー (REQ-001)

```
1. Discord チャンネルに人間がメッセージを投稿
2. on_message で検知
3. フィルタ:
   - Bot自身のメッセージ → 無視（無限ループ防止）
   - 他Botのメッセージ → 無視
   - 部門チャンネル以外 → 無視
4. コンテキスト収集: channel.history(limit=5) で直近5件取得
5. DeptBrain.respond() でAI返答生成
6. 返答をembed形式でチャンネルに投稿
7. send_canvas_event() でCanvas反映
8. 成果物判定 → 該当なら📦チャンネルに転記
```

### 3.2 自発活動フロー (REQ-002)

```
1. Scheduler が4時間ごとに各部門をトリガー
2. 時間帯チェック: 0:00-7:00 JST → スキップ
3. コスト上限チェック: $30/月超 → スキップ
4. activity_type をランダム選択（朝=morning, 昼=idea, 夕=status）
5. DeptBrain.generate_autonomous() で内容生成
6. チャンネルに投稿
7. Canvas反映
8. 成果物判定
```

### 3.3 会議室フロー (REQ-003)

**自律会議**: 部門が自分で議題を持ち込む

```
1. 自発活動で「他部門に相談したい」が出たらトリガー
   or えむが /meeting [議題] で手動トリガー
   or 定期スケジュール（1日1回、14:00 JST）
2. 議題に関連する部門を3-5選択（キーワードマッチ or 指定）
3. ラウンド制:
   Round 1: 各部門が議題に対する初見コメント
   Round 2: 他部門のコメントを踏まえて深掘り
   Round 3: アクション提案
4. 最後にフィル（司令塔）がまとめを投稿
5. まとめの中のアクションアイテムを各部門チャンネルにフォワード
6. Canvas反映
```

### 3.4 成果物保管フロー (REQ-004)

```
1. 部門AI返答/自発活動の投稿を検査
2. 成果物判定:
   - 「【成果物】」タグがある → 成果物
   - 10行以上のコードブロック → 成果物
   - ファイル添付 → 成果物
3. 分類:
   - リレーパネル経由の依頼結果 → 📦ドラの成果物
   - それ以外 → 📦部門の成果物
4. サマリーembedを作成:
   - 部門名・キャラ名
   - 概要（最初の100文字）
   - 元メッセージへのジャンプリンク
5. 📦チャンネルに投稿
```

---

## 4. ファイル構成（変更・新規）

```
scripts/
├── discord_bot.py          # 既存: on_message に返答トリガー追加
├── dept_brain.py           # 新規: AI頭脳エンジン
├── dept_prompts.py         # 新規: 部門システムプロンプト定義
├── dept_scheduler.py       # 新規: 自発活動スケジューラー
└── dept_meeting.py         # 新規: 会議室ディスカッションエンジン

docs/
├── 伝書鳩_取り扱い説明書.md  # 更新: 新機能を反映
└── 開発テストチェックリスト.md # 新規: テスト雛形
```

---

## 5. API・コスト設計

### 使用モデル
- **返答・自発活動**: claude-sonnet-4-20250514（$3/1M input, $15/1M output）
- **会議まとめ**: claude-sonnet-4-20250514

### コスト見積もり
| 項目 | 頻度 | 1回あたり | 日額 | 月額 |
|------|------|----------|------|------|
| 返答 | ~20回/日 | $0.003 | $0.06 | $1.80 |
| 自発活動 | 90回/日 (15部門×6回) | $0.003 | $0.27 | $8.10 |
| 会議 | 1回/日 | $0.06 | $0.06 | $1.80 |
| **合計** | | | **$0.39** | **$11.70** |

月間上限 $30 で十分余裕あり。上限に近づいたら自発活動頻度を落とす。

### レートリミット
- 1分あたり最大10 API呼び出し
- 超えたらキューイングして待つ

---

## 6. 安全設計

### 無限ループ防止（最重要）
```python
# discord_bot.py の on_message 冒頭
if message.author == self.user:
    return  # Bot自身のメッセージは絶対に無視
if message.author.bot:
    return  # 他Botのメッセージも無視
```

### コスト暴走防止
```python
# DeptBrain内
if self._monthly_cost >= self._monthly_cap:
    log.warning("月間コスト上限到達 ($%.2f/$%.2f)。API呼び出しを停止。", ...)
    return None
```

### 応答タイムアウト
- API呼び出しは15秒タイムアウト
- タイムアウト時は「考え中…」embedを投稿してスキップ

---

## 7. 設定ファイル

**`config/autonomous_office.json`** (新規)

```json
{
  "api_model": "claude-sonnet-4-20250514",
  "max_response_tokens": 500,
  "monthly_cost_cap_usd": 30.0,
  "rate_limit_per_minute": 10,
  "autonomous_interval_hours": 4,
  "quiet_hours": {"start": 0, "end": 7},
  "meeting_schedule_hour": 14,
  "context_history_limit": 5,
  "deliverable_min_lines": 10
}
```
