#!/usr/bin/env python3
"""
Discord Bot フルテスト:
1. 残り7チャンネルにテスト送信
2. 会議室で部門キャラ会話シミュレーション
3. 各部門チャンネルにも実際の業務風メッセージ
"""

import asyncio
import os
import discord
from datetime import datetime, timezone, timedelta

BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GUILD_ID = 1494899621460312145
JST = timezone(timedelta(hours=9))

# 全チャンネル
CHANNELS = {
    "司令塔": 1494948235125592206,
    "コンテンツ部": 1494948238292291714,
    "デザイン部": 1494948239751909379,
    "ライティング部": 1494948241215590480,
    "リサーチ部": 1494948243656806450,
    "新規事業部": 1494948244994658376,
    "営業部": 1494948248975314955,
    "広告部": 1494948250065699008,
    "AI投資部": 1494948255392464896,
    "会議室": 1495637503175168092,
    "司令塔ログ": 1495637504580386978,
    "コピーロボット部": 1494948246122922064,
    "フィルコンサル部": 1494948252984807426,
    "タクミX部": 1494948254264201347,
    "不動産部": 1494948256688640040,
    "どらどらSNS部": 1494948258219425832,
    "セキュリティ部": 1494948261289656460,
    "一般": 1494899622253170781,
}

# まだテスト送信してない7チャンネル
REMAINING = [
    "コピーロボット部", "フィルコンサル部", "タクミX部",
    "不動産部", "どらどらSNS部", "セキュリティ部", "一般",
]

# 部門キャラ設定
CHARS = {
    "フィル": {"color": 0xD4AF37, "icon": "🛰️", "role": "司令塔・統括"},
    "リョウ": {"color": 0x00BFFF, "icon": "🔬", "role": "リサーチ部長"},
    "レイ":   {"color": 0xFF6B6B, "icon": "💼", "role": "営業部長"},
    "リック": {"color": 0x9B59B6, "icon": "🎨", "role": "デザイン部長"},
    "カイ":   {"color": 0x2ECC71, "icon": "✍️", "role": "ライティング部長"},
    "エナ":   {"color": 0xE74C3C, "icon": "📢", "role": "広告部長"},
    "アキ":   {"color": 0xF39C12, "icon": "📈", "role": "AI投資部長"},
    "伝書鳩": {"color": 0x95A5A6, "icon": "🐦", "role": "Bridge連絡係"},
    "タダシ": {"color": 0x1ABC9C, "icon": "🚀", "role": "新規事業部長"},
}


def char_embed(name: str, msg: str, **kwargs) -> discord.Embed:
    c = CHARS[name]
    embed = discord.Embed(
        description=msg,
        color=c["color"],
        **kwargs,
    )
    embed.set_author(name=f"{c['icon']} {name}（{c['role']}）")
    return embed


class FullTestBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        super().__init__(intents=intents)
        self.done = asyncio.Event()

    async def on_ready(self):
        print(f"✅ {self.user.name} 起動\n")

        # ステータス設定
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Claude Office Canvas"
            )
        )

        guild = self.get_guild(GUILD_ID)
        if not guild:
            print("❌ Guild見つからず")
            self.done.set()
            return

        # ═══════════════════════════════════════════
        # PART 1: 残り7チャンネルにテスト送信
        # ═══════════════════════════════════════════
        print("=" * 60)
        print("PART 1: 残り7チャンネルへテスト送信")
        print("=" * 60)

        now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")
        for name in REMAINING:
            ch = guild.get_channel(CHANNELS[name])
            if not ch:
                print(f"  ❌ {name} — チャンネル見つからず")
                continue
            embed = discord.Embed(
                title="🛰️ Commander Bridge 接続テスト",
                description=f"**{name}** チャンネルへの接続を確認しました。",
                color=0xD4AF37,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="ステータス", value="✅ 正常接続", inline=True)
            embed.add_field(name="テスト時刻", value=now, inline=True)
            embed.set_footer(text="Claude Office × Commander Bridge")
            await ch.send(embed=embed)
            print(f"  ✅ {name} — 送信OK")
            await asyncio.sleep(0.5)

        # ═══════════════════════════════════════════
        # PART 2: 各部門チャンネルに業務風メッセージ
        # ═══════════════════════════════════════════
        print(f"\n{'='*60}")
        print("PART 2: 各部門に業務メッセージ")
        print("=" * 60)

        dept_messages = {
            "リサーチ部": char_embed("リョウ",
                "**📋 本日のリサーチ速報**\n\n"
                "• Anthropic Claude 4.6 — マルチエージェント連携の新パターン確認\n"
                "• AI Agent市場 — 2026年Q2で前年比340%成長\n"
                "• 競合動向 — Devin/Cursor/Windsurf の最新アップデート比較完了\n\n"
                "**アクション**: コンテンツ部に台本ネタとして共有予定"
            ),
            "デザイン部": char_embed("リック",
                "**🎨 制作状況アップデート**\n\n"
                "• LP v3 — WarmBizテーマで最終調整中\n"
                "• スライドHTML — 新テンプレ5種追加完了\n"
                "• Canvas用キャラPNG — 全12体制作済み\n\n"
                "**品質**: L2チェック通過済み。ドラ確認待ち"
            ),
            "ライティング部": char_embed("カイ",
                "**✍️ コピー進捗**\n\n"
                "• フィルコンサルLP — taiyo-analyzer **87点** 🟢 納品基準クリア\n"
                "• ステップメール5通 — 第3通まで完成、第4通作成中\n"
                "• X投稿ストック — 15本追加（タクミ用）\n\n"
                "**次**: 第4通の「投資/費用対効果」キーワード埋め込み"
            ),
            "営業部": char_embed("レイ",
                "**💼 営業レポート**\n\n"
                "• 新規リード: 3件（温2/冷1）\n"
                "• 商談予定: 明日14:00 Zoom（不動産AI秘書デモ）\n"
                "• playbook準備: sales_playbook_engine.py で生成済み\n\n"
                "**判断依頼**: 冷リードにフォーム営業文面送るか？"
            ),
            "広告部": char_embed("エナ",
                "**📢 広告運用メモ**\n\n"
                "• FB広告テスト — PASフレームワーク3パターンA/B中\n"
                "• CPC平均: ¥180（業界平均¥250 → **28%下回る**）\n"
                "• LP遷移率: 4.2%\n\n"
                "**提案**: 勝ちパターン確定後、予算2倍に引き上げ"
            ),
            "AI投資部": char_embed("アキ",
                "**📈 マーケット速報**\n\n"
                "• BTC: 76,038ドル（24h: +1.2%）\n"
                "• ETH/BTC比率: 安定域\n"
                "• DeFi TVL: 前週比 +5.3%\n"
                "• 注目: 新プロトコルのオンチェーンデータに異常な資金流入\n\n"
                "**アクション**: Tier 2分析に移行。Telegram配信文準備中"
            ),
            "コンテンツ部": char_embed("フィル",
                "**🎬 コンテンツ在庫状況**\n\n"
                "| チャンネル | 台本在庫 | 目標 |\n"
                "| eBay輸出M | 8本 | 10本 |\n"
                "| AIエージェントM | 12本 | 10本 ✅ |\n"
                "| AI副業 | 6本 | 10本 |\n\n"
                "**優先**: AI副業チャンネルのネタ補充。リサーチ部に依頼済み"
            ),
            "新規事業部": char_embed("タダシ",
                "**🚀 新規事業検討ログ**\n\n"
                "• 検討中: ソースチェーン分析の商品化\n"
                "• フェーズ: アイデア段階 → ドラ壁打ち待ち\n"
                "• 判断基準: 既存7ラインの補強になるか？\n\n"
                "**メモ**: 「8本目を安易に追加しない」原則厳守"
            ),
        }

        for dept_name, embed in dept_messages.items():
            ch = guild.get_channel(CHANNELS[dept_name])
            if ch:
                await ch.send(embed=embed)
                print(f"  ✅ {dept_name} — 業務メッセージ送信")
                await asyncio.sleep(0.5)

        # ═══════════════════════════════════════════
        # PART 3: 会議室で部門横断ブレスト会話
        # ═══════════════════════════════════════════
        print(f"\n{'='*60}")
        print("PART 3: 会議室でブレスト会話")
        print("=" * 60)

        meeting_ch = guild.get_channel(CHANNELS["会議室"])
        if not meeting_ch:
            print("  ❌ 会議室チャンネル見つからず")
            self.done.set()
            return

        # 会議開始
        header = discord.Embed(
            title="📋 部門横断ブレスト会議",
            description=(
                "**議題**: Claude Office × Discord連携で何ができるか？\n"
                "**参加者**: フィル / リョウ / レイ / リック / カイ / エナ / アキ / タダシ / 伝書鳩\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=0xD4AF37,
            timestamp=datetime.now(timezone.utc),
        )
        await meeting_ch.send(embed=header)
        await asyncio.sleep(1)

        # 会話シーケンス
        conversation = [
            ("フィル",
             "今回の議題は**Claude Office CanvasとDiscordの連携**だ。\n"
             "現状、Canvasでエージェントの動きが可視化できてる。"
             "これにDiscordを繋げると何ができるか、各部門からアイデア出してくれ。"),

            ("リョウ",
             "リサーチ部としては、**リアルタイムリサーチ速報のCanvas表示**が欲しい。\n"
             "Discord側で「SSS級ネタ発見」って投稿したら、Canvas上のリサーチデスクに"
             "速報バッジが光るとか。ドラがCanvas見てるだけで重要度がわかる仕組み。"),

            ("レイ",
             "営業部は**商談ステータスの可視化**だな。\n"
             "Discord営業部チャンネルで「商談完了・成約」って投稿したら、"
             "Canvas上で営業デスクに✅が出る。パイプラインの動きがリアルタイムで見える。"),

            ("リック",
             "デザイン部としては**制作物のプレビュー連携**。\n"
             "LP作り終わったらDiscordに投稿 → Canvas上のデザインデスクに"
             "サムネが表示されて、ドラがCanvas上からワンクリックでプレビューできる。"),

            ("エナ",
             "広告部は**CPCやCVRの数値をCanvasのメインスクリーンに表示**したい。\n"
             "Discordでレポート投稿 → Canvas右上のSTOCKS風パネルに広告KPIが"
             "リアルタイム表示。赤字は赤、黒字は緑で。"),

            ("アキ",
             "AI投資部的には**BTC/ETH価格をCanvasの株価ボードにリアルタイム連携**。\n"
             "今もSTOCKSパネルあるけど、Discordの投資部チャンネルからの"
             "アラート（急騰・急落）がCanvasに即反映されると最高。"),

            ("カイ",
             "ライティング部は**台本完成通知のCanvas連携**。\n"
             "「台本v3完成・taiyo-analyzer 92点」ってDiscordに投稿したら、"
             "Canvas上のライティングデスクに品質スコアバッジが出る。"
             "85点以上は緑、未満は黄色で。"),

            ("タダシ",
             "新規事業部からは**壁打ちログの自動記録**。\n"
             "会議室チャンネルでの会話を自動でObsidianに保存。"
             "ドラが後から「あのアイデアなんだっけ」って時にすぐ引ける。"),

            ("伝書鳩",
             "🐦 伝書鳩としては、まさに**自分の役割そのもの**ですね。\n"
             "Discord ↔ Canvas の橋渡し。部門間の連絡を自動で届ける。\n"
             "「リサーチ部からコンテンツ部へ台本ネタ共有」みたいな"
             "クロス部門メッセージを自動でルーティング。"),

            ("フィル",
             "いいアイデアが出揃った。整理すると:\n\n"
             "**Phase 1（すぐやれる）**\n"
             "• Discord投稿 → Canvas通知バッジ\n"
             "• 部門間メッセージ自動ルーティング\n\n"
             "**Phase 2（次にやる）**\n"
             "• 広告KPI/仮想通貨価格のリアルタイムパネル\n"
             "• 台本品質スコアの可視化\n\n"
             "**Phase 3（将来）**\n"
             "• LP/制作物のCanvasプレビュー\n"
             "• 壁打ちログの自動Obsidian保存\n\n"
             "ドラ、どれから着手する？"),
        ]

        for name, msg in conversation:
            embed = char_embed(name, msg)
            await meeting_ch.send(embed=embed)
            char_info = CHARS[name]
            print(f"  {char_info['icon']} {name}: {msg[:40]}...")
            await asyncio.sleep(2)

        # 司令塔ログにサマリー
        log_ch = guild.get_channel(CHANNELS["司令塔ログ"])
        if log_ch:
            log_embed = discord.Embed(
                title="📝 会議ログ保存",
                description=(
                    "**議題**: Claude Office × Discord連携アイデア出し\n"
                    "**参加**: 9名\n"
                    "**結論**: 3フェーズに分けて実装\n"
                    "**次のアクション**: ドラの優先順位判断待ち"
                ),
                color=0x00FF88,
                timestamp=datetime.now(timezone.utc),
            )
            await log_ch.send(embed=log_embed)
            print(f"\n  ✅ 司令塔ログに会議サマリー保存")

        print(f"\n{'='*60}")
        print(f"✅ 全テスト完了!")
        print(f"  • 残り7チャンネル送信: 完了")
        print(f"  • 8部門に業務メッセージ: 完了")
        print(f"  • 会議室ブレスト会話: {len(conversation)}発言")
        print(f"{'='*60}")

        self.done.set()


async def main():
    bot = FullTestBot()
    task = asyncio.create_task(bot.start(BOT_TOKEN))

    try:
        await asyncio.wait_for(bot.done.wait(), timeout=120)
    except asyncio.TimeoutError:
        print("⏰ タイムアウト")

    await asyncio.sleep(2)
    await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
