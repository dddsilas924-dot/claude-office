#!/usr/bin/env python3
"""
Discord Bot メッセージ受信テスト — Commander Bridge Bot
メッセージを受信して応答できるか確認する
"""

import asyncio
import os
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta

BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GUILD_ID = 1494899621460312145

# 全チャンネルマッピング
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

# チャンネルID→名前の逆引き
CH_ID_TO_NAME = {v: k for k, v in CHANNELS.items()}

JST = timezone(timedelta(hours=9))


class ListenBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.ready_event = asyncio.Event()
        self.msg_received = asyncio.Event()
        self.received_messages = []

    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self):
        print(f"✅ Bot起動: {self.user.name}#{self.user.discriminator}")
        print(f"   メッセージ待機中...\n")

        # Botステータス設定
        activity = discord.Activity(
            type=discord.ActivityType.watching,
            name="Claude Office Canvas"
        )
        await self.change_presence(
            status=discord.Status.online,
            activity=activity
        )
        print(f"✅ Botステータス設定: 🟢 オンライン | Watching Claude Office Canvas")
        self.ready_event.set()

    async def on_message(self, message: discord.Message):
        # Bot自身のメッセージは無視
        if message.author == self.user:
            return

        ch_name = CH_ID_TO_NAME.get(message.channel.id, message.channel.name)
        now = datetime.now(JST).strftime("%H:%M:%S")

        print(f"📨 [{now}] #{ch_name} | {message.author.display_name}: {message.content}")
        self.received_messages.append({
            "channel": ch_name,
            "author": message.author.display_name,
            "content": message.content,
            "time": now,
        })

        # 応答パターン
        content_lower = message.content.lower().strip()

        if content_lower in ("テスト", "test", "ping"):
            embed = discord.Embed(
                title="🛰️ Commander Bridge 応答",
                description="Pong! Bot正常稼働中です。",
                color=0xD4AF37,
            )
            embed.add_field(name="レイテンシ", value=f"{round(self.latency * 1000)}ms", inline=True)
            embed.add_field(name="チャンネル", value=f"#{ch_name}", inline=True)
            await message.reply(embed=embed)
            print(f"  → 応答送信: Pong!")

        elif content_lower in ("ステータス", "status"):
            embed = discord.Embed(
                title="📊 Commander Bridge ステータス",
                color=0x00FF88,
            )
            embed.add_field(name="Bot", value="🟢 オンライン", inline=True)
            embed.add_field(name="Canvas接続", value="🔗 待機中", inline=True)
            embed.add_field(name="部門数", value=f"{len(CHANNELS)}チャンネル", inline=True)
            embed.add_field(
                name="登録部門",
                value="司令塔 | コンテンツ | デザイン | ライティング | リサーチ | 営業 | 広告 | AI投資 | 新規事業 | フィルコンサル | タクミX | 不動産 | セキュリティ",
                inline=False,
            )
            await message.reply(embed=embed)
            print(f"  → 応答送信: ステータス")

        elif content_lower in ("ヘルプ", "help"):
            embed = discord.Embed(
                title="🛰️ Commander Bridge ヘルプ",
                description="以下のコマンドが使えます:",
                color=0x5865F2,
            )
            embed.add_field(name="テキストコマンド", value=(
                "• `テスト` / `ping` — Bot応答確認\n"
                "• `ステータス` / `status` — 稼働状況\n"
                "• `ヘルプ` / `help` — このヘルプ\n"
                "• `部門一覧` — 全チャンネル一覧"
            ), inline=False)
            embed.add_field(name="スラッシュコマンド", value=(
                "• `/status` — ステータス表示\n"
                "• `/ask [質問]` — Bridgeに質問\n"
                "• `/bridge [アクション]` — Canvas連携"
            ), inline=False)
            await message.reply(embed=embed)
            print(f"  → 応答送信: ヘルプ")

        elif content_lower in ("部門一覧", "channels"):
            lines = []
            for name, ch_id in sorted(CHANNELS.items()):
                ch = self.get_channel(ch_id)
                status = "✅" if ch else "❌"
                lines.append(f"{status} **{name}** — <#{ch_id}>")
            embed = discord.Embed(
                title="📋 全チャンネル一覧",
                description="\n".join(lines),
                color=0xD4AF37,
            )
            await message.reply(embed=embed)
            print(f"  → 応答送信: 部門一覧")

        else:
            # それ以外のメッセージにはリアクションで応答
            await message.add_reaction("👀")
            print(f"  → リアクション: 👀")

        self.msg_received.set()


async def main():
    bot = ListenBot()

    # スラッシュコマンド定義
    @bot.tree.command(name="status", description="Commander Bridgeのステータスを表示")
    async def status_cmd(interaction: discord.Interaction):
        embed = discord.Embed(title="🛰️ Status", color=0xD4AF37)
        embed.add_field(name="接続", value="✅ オンライン", inline=True)
        embed.add_field(name="レイテンシ", value=f"{round(bot.latency * 1000)}ms", inline=True)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="ask", description="Commander Bridgeに質問する")
    @app_commands.describe(question="質問内容")
    async def ask_cmd(interaction: discord.Interaction, question: str):
        await interaction.response.send_message(
            f"📡 受信: `{question}`\n🛰️ Commander Bridge処理中...\n\n"
            f"（本番環境では Claude Code に転送されます）"
        )

    @bot.tree.command(name="bridge", description="Canvas↔Discord連携")
    @app_commands.describe(action="アクション")
    @app_commands.choices(action=[
        app_commands.Choice(name="canvas状況", value="canvas"),
        app_commands.Choice(name="エージェント一覧", value="agents"),
        app_commands.Choice(name="タスク一覧", value="tasks"),
    ])
    async def bridge_cmd(interaction: discord.Interaction, action: app_commands.Choice[str]):
        responses = {
            "canvas": "🖥️ Canvas: 稼働中 | デスク: 12 | 稼働エージェント: 待機中",
            "agents": "👥 フィル(司令塔) | リョウ(リサーチ) | レイ(営業) | リック(デザイン) | カイ(ライティング) | エナ(広告) | 伝書鳩(Bridge)",
            "tasks": "📋 進行中タスク:\n• Discord連携テスト ✅\n• Canvas表示最適化\n• 本番デプロイ準備",
        }
        await interaction.response.send_message(responses.get(action.value, "不明"))

    # Bot起動（バックグラウンド）
    task = asyncio.create_task(bot.start(BOT_TOKEN))

    # 起動待ち
    await bot.ready_event.wait()

    # Botからテストメッセージを自分で送って、受信ログを確認
    guild = bot.get_guild(GUILD_ID)
    test_ch = guild.get_channel(CHANNELS["会議室"])

    print(f"\n🧪 自己テスト: 会議室にメッセージ送信...")
    await test_ch.send("🧪 **自動テスト**: メッセージ受信テスト開始。\n"
                       "このチャンネルでメッセージを送ると Bot が応答します。\n\n"
                       "**テスト用コマンド**: `テスト` `ステータス` `ヘルプ` `部門一覧`")

    # 90秒間メッセージ待機
    print(f"\n⏳ 90秒間メッセージを待機中... (Discordでメッセージを送ってテストしてください)")
    print(f"   コマンド例: テスト, ステータス, ヘルプ, 部門一覧\n")

    try:
        await asyncio.wait_for(asyncio.sleep(90), timeout=95)
    except asyncio.TimeoutError:
        pass

    # 結果レポート
    print(f"\n{'='*60}")
    print(f"📊 メッセージ受信テスト結果")
    print(f"{'='*60}")
    if bot.received_messages:
        for msg in bot.received_messages:
            print(f"  [{msg['time']}] #{msg['channel']} — {msg['author']}: {msg['content']}")
    else:
        print(f"  （受信メッセージなし — ユーザーからのメッセージ待ち）")
    print(f"{'='*60}")

    await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
