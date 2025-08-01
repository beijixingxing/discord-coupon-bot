# main.py
import os
import discord
import asyncio
import logging
from dotenv import load_dotenv
from datetime import datetime
from zoneinfo import ZoneInfo
from bot import CouponBot

# --- 日志配置 ---
class TimezoneFormatter(logging.Formatter):
    """自定义日志格式化类，用于显示指定时区的时间。"""
    def __init__(self, fmt=None, datefmt=None, tz_name="Asia/Shanghai"):
        super().__init__(fmt, datefmt)
        try:
            self.tz = ZoneInfo(tz_name)
        except Exception:
            self.tz = None

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, self.tz)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat()

def setup_logging():
    """配置根日志记录器。"""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    console_handler = logging.StreamHandler()
    log_format = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    formatter = TimezoneFormatter(log_format, date_format, tz_name="Asia/Shanghai")
    console_handler.setFormatter(formatter)

    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    root_logger.addHandler(console_handler)

    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)

logger = logging.getLogger('main')

# --- 初始化 ---
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
GUILD_IDS_STR = os.getenv('TRUSTED_GUILD_ID')
ADMIN_IDS_STR = os.getenv('ADMIN_USER_IDS')

DEBUG_GUILDS = [int(gid.strip()) for gid in GUILD_IDS_STR.split(',')] if GUILD_IDS_STR else None

# 解析管理员ID
ADMIN_USER_IDS = set()
if ADMIN_IDS_STR:
    try:
        ADMIN_USER_IDS = {int(uid.strip()) for uid in ADMIN_IDS_STR.split(',') if uid.strip().isdigit()}
    except ValueError:
        logger.critical("ADMIN_USER_IDS 格式错误，请确保是由逗号分隔的纯数字ID。")
        exit(1)

if not ADMIN_USER_IDS:
    logger.critical("ADMIN_USER_IDS 在 .env 文件中未配置或格式错误。机器人无法启动，因为没有管理员。")
    exit(1)

# --- 主程序，带自动重连 ---
async def main():
    setup_logging()

    if not TOKEN:
        logger.critical("DISCORD_BOT_TOKEN 在 .env 文件中未找到。机器人无法启动。")
        return

    if DEBUG_GUILDS:
        logger.info(f"机器人将只在 {len(DEBUG_GUILDS)} 个受信任的服务器上注册命令: {DEBUG_GUILDS}")
    else:
        logger.warning("未在 .env 中配置 TRUSTED_GUILD_ID。命令将被注册为全局命令，可能需要长达1小时生效。")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = False
    intents.presences = False
    cache_flags = discord.MemberCacheFlags.none()

    logger.info(f"已加载 {len(ADMIN_USER_IDS)} 个管理员ID。")

    bot = None # 在循环外初始化
    while True:
        try:
            bot = CouponBot(
                intents=intents,
                member_cache_flags=cache_flags,
                debug_guilds=DEBUG_GUILDS,
                admin_user_ids=ADMIN_USER_IDS
            )
            logger.info("正在尝试连接到 Discord...")
            await bot.start(TOKEN)
        
        except (discord.errors.ConnectionClosed, asyncio.CancelledError, discord.errors.LoginFailure) as e:
            logger.warning(f"连接已断开或登录失败: {e}。准备重连...")
        except Exception:
            logger.exception("机器人在运行时遇到无法恢复的严重错误。准备重启...")
        
        finally:
            reconnect_delay = 15
            if bot and not bot.is_closed():
                # 尝试从已加载的配置中获取延迟时间
                reconnect_delay = bot.config.get('bot_settings', {}).get('reconnect_delay_seconds', 15)
                await bot.close()
            
            logger.info(f"将在 {reconnect_delay} 秒后重连...")
            await asyncio.sleep(reconnect_delay)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n机器人已被用户手动关闭。")
