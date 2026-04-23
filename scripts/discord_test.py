#!/usr/bin/env python3
"""
Discord Bot接続テスト — Commander Bridge Bot
全チャンネルにテストメッセージ送信 + Bot情報確認
"""

import asyncio
import os
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta

# ── Credentials ──────────────────────────────────────────
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GUILD_ID = 1494899621460312145

# ── Channel Map ──────────────────────────────────────────
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
}

JST = timezone(timedelta(hours=9))


class TestBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.test_done = asyncio.Event()

    async def on_ready(self):
        print(f"\n{'='*60}")
        print(f"✅ Bot接続成功!")
        print(f"   Bot名: {self.user.name}#{self.user.discriminator}")
        print(f"   Bot ID: {self.user.id}")
        print(f"   接続Guild数: {len(self.guilds)}")
        print(f"{'='*60}\n")

        # Guild情報
        guild = self.get_guild(GUILD_ID)
        if not guild:
            print(f"❌ Guild {GUILD_ID} が見つかりません")
            self.test_done.set()
            return

        print(f"📡 Guild: {guild.name}")
        print(f"   メンバー数: {guild.member_count}")
        print(f"   チャンネル数: {len(guild.channels)}")
        print(f"   カテゴリ数: {len(guild.categories)}")
        print()

        # カテゴリ構造表示
        print("📁 サーバー構造:")
        for category in guild.categories:
            print(f"  ┌ {category.name}")
            for ch in category.channels:
                ch_type = "📝" if isinstance(ch, discord.TextChannel) else "🔊"
                print(f"  │  {ch_type} {ch.name} ({ch.id})")
            print(f"  └")
        print()

        # テストメッセージ送信
        now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")
        success = 0
        fail = 0

        print(f"📨 テストメッセージ送信開始...")
        for name, ch_id in CHANNELS.items():
            channel = guild.get_channel(ch_id)
            if not channel:
                print(f"  ❌ {name} (ID: {ch_id}) — チャンネル見つからず")
                fail += 1
                continue

            try:
                embed = discord.Embed(
                    title=f"🛰️ Commander Bridge 接続テスト",
                    description=f"**{name}** チャンネルへの接続を確認しました。",
                    color=0xD4AF37,  # Gold
                    timestamp=datetime.now(timezone.utc),
                )
                embed.add_field(
                    name="ステータス",
                    value="✅ 正常接続",
                    inline=True,
                )
                embed.add_field(
                    name="テスト時刻",
                    value=now,
                    inline=True,
                )
                embed.add_field(
                    name="Bot",
                    value=f"{self.user.name} (v1.0)",
                    inline=True,
                )
                embed.set_footer(text="Claude Office × Commander Bridge")

                await channel.send(embed=embed)
                print(f"  ✅ {name} — 送信成功")
                success += 1
                await asyncio.sleep(0.5)  # Rate limit回避

            except discord.Forbidden:
                print(f"  ❌ {name} — 権限不足")
                fail += 1
            except Exception as e:
                print(f"  ❌ {name} — エラー: {e}")
                fail += 1

        print(f"\n{'='*60}")
        print(f"📊 テスト結果: {success}成功 / {fail}失敗 / {len(CHANNELS)}チャンネル")
        print(f"{'='*60}")

        # 司令塔にサマリー送信
        cmd_ch = guild.get_channel(CHANNELS["司令塔"])
        if cmd_ch:
            summary_embed = discord.Embed(
                title="📊 接続テスト完了レポート",
                description=f"全 {len(CHANNELS)} チャンネルへのテスト送信が完了しました。",
                color=0x00FF88 if fail == 0 else 0xFF4444,
            )
            summary_embed.add_field(
                name="結果",
                value=f"✅ 成功: {success}\n❌ 失敗: {fail}",
                inline=True,
            )
            summary_embed.add_field(
                name="次のステップ",
                value="• スラッシュコマンド動作確認\n• Canvas連携テスト\n• 本番運用開始",
                inline=True,
            )
            await cmd_ch.send(embed=summary_embed)

        # スラッシュコマンドをGuildに同期
        print("\n⚙️ スラッシュコマンド同期中...")
        try:
            guild_obj = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild_obj)
            synced = await self.tree.sync(guild=guild_obj)
            print(f"  ✅ {len(synced)} コマンド同期完了")
        except Exception as e:
            print(f"  ⚠️ コマンド同期: {e}")

        self.test_done.set()

    async def run_test(self):
        """テスト実行して完了したら終了"""
        async with self:
            await self.start(BOT_TOKEN)


async def main():
    bot = TestBot()

    # /status コマンド追加
    @bot.tree.command(name="status", description="Commander Bridgeのステータスを表示")
    async def status_cmd(interaction: discord.Interaction):
        embed = discord.Embed(
            title="🛰️ Commander Bridge Status",
            color=0xD4AF37,
        )
        embed.add_field(name="接続", value="✅ オンライン", inline=True)
        embed.add_field(name="Canvas", value="🔗 接続中", inline=True)
        embed.add_field(name="エージェント数", value="12部門", inline=True)
        await interaction.response.send_message(embed=embed)

    # /ask コマンド追加
    @bot.tree.command(name="ask", description="Commander Bridgeに質問する")
    @app_commands.describe(question="質問内容")
    async def ask_cmd(interaction: discord.Interaction, question: str):
        await interaction.response.send_message(
            f"📡 受信: `{question}`\n⏳ Commander Bridge処理中..."
        )

    # /bridge コマンド追加
    @bot.tree.command(name="bridge", description="Canvas↔Discord連携コマンド")
    @app_commands.describe(action="実行するアクション")
    @app_commands.choices(action=[
        app_commands.Choice(name="canvas状況", value="canvas"),
        app_commands.Choice(name="エージェント一覧", value="agents"),
        app_commands.Choice(name="タスク一覧", value="tasks"),
    ])
    async def bridge_cmd(interaction: discord.Interaction, action: app_commands.Choice[str]):
        if action.value == "canvas":
            await interaction.response.send_message("🖥️ Canvas: 稼働中 | エージェント: 12名 | タスク: 3件進行中")
        elif action.value == "agents":
            agents = "フィル(司令塔) | リョウ(リサーチ) | レイ(営業) | リック(デザイン) | カイ(ライティング) | エナ(広告)"
            await interaction.response.send_message(f"👥 アクティブエージェント:\n{agents}")
        elif action.value == "tasks":
            await interaction.response.send_message("📋 進行中タスク:\n• Discord連携テスト\n• Canvas表示最適化\n• HMAC認証検証")

    # Bot起動
    task = asyncio.create_task(bot.run_test())

    # テスト完了を待つ（最大60秒）
    try:
        await asyncio.wait_for(bot.test_done.wait(), timeout=60)
    except asyncio.TimeoutError:
        print("⏰ タイムアウト（60秒）")

    # 少し待ってから終了（メッセージ送信完了待ち）
    await asyncio.sleep(2)
    await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
