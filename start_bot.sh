#!/bin/bash
# Discord Bot ランチャー（多重起動防止付き）
cd "$(dirname "$0")"

# 既存Botプロセスを全て殺す（多重起動防止）
pkill -f "python3 scripts/discord_bot.py" 2>/dev/null
pkill -f "python3.*discord_bot.py" 2>/dev/null
sleep 2

echo "=== ミスターDオフィス Discord Bot 起動 ==="
echo "ディレクトリ: $(pwd)"
echo "時刻: $(date)"
echo ""
caffeinate -d python3 scripts/discord_bot.py 2>&1 | tee logs/bot_$(date +%Y%m%d_%H%M%S).log
