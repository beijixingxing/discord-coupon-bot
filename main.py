# main.py
import os
import discord
import asyncio
import logging
from dotenv import load_dotenv
from bot import CouponBot

# --- 初始化日志 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
logger = logging.getLogger('main')

# --- 初始化 ---
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
# <<< 优化: 从 .env 文件安全地读取受信任的服务器ID
# 处理多个服务器ID的情况
GUILD_IDS_STR = os.getenv('TRUSTED_GUILDS')
DEBUG_GUILDS = [int(gid.strip()) for gid in GUILD_IDS_STR.split(',')] if GUILD_IDS_STR else None

# --- 主程序，带自动重连 ---
async def main():
    if not TOKEN:
        logger.critical("DISCORD_BOT_TOKEN 在 .env 文件中未找到。机器人无法启动。")
        return

    # <<< 优化: 如果设置了受信任服务器，清晰地告知用户
    if DEBUG_GUILDS:
        logger.info(f"机器人将只在 {len(DEBUG_GUILDS)} 个受信任的服务器上注册命令: {DEBUG_GUILDS}")
    else:
        logger.warning("未在 .env 中配置 TRUSTED_GUILD_ID。命令将被注册为全局命令，可能需要长达1小时生效。")

    intents = discord.Intents.default()
    intents.members = False
    intents.presences = False
    cache_flags = discord.MemberCacheFlags.none()

    while True:
        # <<< 关键修正: 将 debug_guilds 参数传递给机器人实例
        bot = CouponBot(intents=intents, member_cache_flags=cache_flags, debug_guilds=DEBUG_GUILDS)
        try:
            logger.info("正在尝试连接到 Discord...")
            await bot.start(TOKEN)
      
        except (discord.errors.ConnectionClosed, asyncio.exceptions.CancelledError, discord.errors.LoginFailure) as e:
            logger.warning(f"连接已断开或登录失败: {e}。将在 15 秒后重连...")
            if not bot.is_closed():
                await bot.close()
            await asyncio.sleep(15)
        except Exception:
            logger.exception("机器人在启动时遇到无法恢复的严重错误。将在 15 秒后重启...")
            if 'bot' in locals() and not bot.is_closed():
                await bot.close()
            await asyncio.sleep(15)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n机器人已被用户手动关闭。")
