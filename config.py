import os
from dotenv import load_dotenv
from datetime import time

load_dotenv()

class Config:
    # 项目版本
    VERSION = "1.0.0"

    # 数据库配置
    DB_FILE = "coupon_bot.db"
    DATABASE_URL = f"sqlite+aiosqlite:///{DB_FILE}"
    
    # 时区配置
    TIMEZONE_OFFSET = 8  # UTC+8
    
    # 缓存配置
    CACHE_UPDATE_INTERVAL = 5  # 分钟
    
    # 备份配置
    BACKUP_TIME = time(3, 0)  # 凌晨3点
    MAX_BACKUPS = 5
    
    # Discord配置
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    ADMIN_ROLE = "管理组"
    TRUSTED_GUILDS = [int(gid) for gid in os.getenv('TRUSTED_GUILDS', '').split(',') if gid]
    
    # 兑换券配置
    DEFAULT_COOLDOWN = 168  # 小时
    DEFAULT_EXPIRY_DAYS = 30  # 默认有效期