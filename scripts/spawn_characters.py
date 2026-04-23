#!/usr/bin/env python3
"""
キャラクターをCanvas上に表示する — HMAC署名付きBridgeイベント送信

全部門キャラ（フィル、リョウ、レイ、リック、カイ、エナ、アキ、タダシ、伝書鳩）を
external_event APIで送信してCanvas上にスプライト表示させる。
"""

import hashlib
import hmac
import json
import time
import urllib.request

API_URL = "http://localhost:8000/api/v1/external_event"
SECRET = "EqeUaSghh0inJxagunS-Zoyo91_683Ja-6cmWHkNBmQ"

# 部門キャラ定義（dept_id → CHARACTER_FILES のキーと一致させる）
CHARACTERS = [
    {
        "dept_id": "commander",
        "display_name": "🛰️ フィル（司令塔）",
        "agent_color": "#D4AF37",
        "event_kind": "ASK_STARTED",
        "message": "全部門、稼働状況を報告せよ。Canvas連携テスト中。",
        "model": "opus",
    },
    {
        "dept_id": "research",
        "display_name": "🔬 リョウ（リサーチ）",
        "agent_color": "#00BFFF",
        "event_kind": "ASK_STARTED",
        "message": "Anthropic Claude 4.6の新機能を調査中。SSS級の発見あり。",
        "model": "sonnet",
    },
    {
        "dept_id": "sales",
        "display_name": "💼 レイ（営業）",
        "agent_color": "#FF6B6B",
        "event_kind": "ASK_STARTED",
        "message": "新規リード3件。明日の商談準備中。",
        "model": "sonnet",
    },
    {
        "dept_id": "design",
        "display_name": "🎨 リック（デザイン）",
        "agent_color": "#9B59B6",
        "event_kind": "ASK_STARTED",
        "message": "LP v3 WarmBizテーマ最終調整。品質L2チェック通過。",
        "model": "sonnet",
    },
    {
        "dept_id": "content",
        "display_name": "🎬 コンテンツ部",
        "agent_color": "#E67E22",
        "event_kind": "ASK_STARTED",
        "message": "AI副業チャンネルの台本ネタ補充中。在庫6本→目標10本。",
        "model": "sonnet",
    },
    {
        "dept_id": "writing",
        "display_name": "✍️ カイ（ライティング）",
        "agent_color": "#2ECC71",
        "event_kind": "ASK_STARTED",
        "message": "ステップメール第4通作成中。taiyo-analyzer 87点。",
        "model": "sonnet",
    },
    {
        "dept_id": "advertising",
        "display_name": "📢 エナ（広告）",
        "agent_color": "#E74C3C",
        "event_kind": "ASK_STARTED",
        "message": "FB広告PASパターンA/Bテスト中。CPC ¥180で業界平均以下。",
        "model": "sonnet",
    },
    {
        "dept_id": "ai_investment",
        "display_name": "📈 アキ（AI投資）",
        "agent_color": "#F39C12",
        "event_kind": "ASK_STARTED",
        "message": "BTC 76,038ドル。DeFi TVL前週比+5.3%。Tier 2分析に移行。",
        "model": "sonnet",
    },
    {
        "dept_id": "new_biz",
        "display_name": "🚀 タダシ（新規事業）",
        "agent_color": "#1ABC9C",
        "event_kind": "ASK_STARTED",
        "message": "ソースチェーン分析の商品化検討中。どらさん壁打ち待ち。",
        "model": "sonnet",
    },
    {
        "dept_id": "bridge",
        "display_name": "🐦 伝書鳩（Bridge）",
        "agent_color": "#95A5A6",
        "event_kind": "ASK_STARTED",
        "message": "Discord↔Canvas連携テスト完了。全18チャンネル接続OK。",
        "model": "haiku",
    },
    {
        "dept_id": "phil_consulting",
        "display_name": "📚 フィルコンサル",
        "agent_color": "#8E44AD",
        "event_kind": "ASK_STARTED",
        "message": "脳の棚卸しカリキュラムSTEP2セットアップ中。",
        "model": "sonnet",
    },
    {
        "dept_id": "security",
        "display_name": "🛡️ セキュリティ",
        "agent_color": "#34495E",
        "event_kind": "ASK_STARTED",
        "message": "9項目セキュリティチェック実行中。CVE監視正常。",
        "model": "haiku",
    },
]


def send_bridge_event(char: dict) -> bool:
    """HMAC署名付きでexternal_eventを送信"""
    payload = {
        "session_id": "bridge_canvas_demo",
        "dept_id": char["dept_id"],
        "display_name": char["display_name"],
        "agent_color": char["agent_color"],
        "event_kind": char["event_kind"],
        "message": char["message"],
        "model": char.get("model", "sonnet"),
    }

    body_bytes = json.dumps(payload).encode("utf-8")
    timestamp = str(int(time.time()))
    signing_input = f"{timestamp}.".encode() + body_bytes
    signature = hmac.new(
        SECRET.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).hexdigest()

    req = urllib.request.Request(
        API_URL,
        data=body_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Bridge-Signature": f"sha256={signature}",
            "X-Bridge-Timestamp": timestamp,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            if status in (200, 202):
                return True
            else:
                print(f"  ⚠️ HTTP {status}")
                return False
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False


def main():
    print("=" * 60)
    print("🛰️ キャラクターCanvas表示 — Bridge Event送信")
    print("=" * 60)
    print()

    success = 0
    fail = 0

    for char in CHARACTERS:
        name = char["display_name"]
        ok = send_bridge_event(char)
        if ok:
            print(f"  ✅ {name} — Canvas表示OK")
            success += 1
        else:
            print(f"  ❌ {name} — 失敗")
            fail += 1
        time.sleep(0.3)

    print()
    print("=" * 60)
    print(f"📊 結果: {success}体表示 / {fail}失敗 / {len(CHARACTERS)}キャラ")
    print("=" * 60)
    print()
    print("Canvas (localhost:3000) を確認してください。")
    print("キャラクターがデスクに座っているはずです。")


if __name__ == "__main__":
    main()
