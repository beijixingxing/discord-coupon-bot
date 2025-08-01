import discord
from discord.ext import commands
import logging
from typing import List

logger = logging.getLogger('utils')

async def project_autocompleter(ctx: discord.AutocompleteContext) -> List[str]:
    """为需要项目名称的命令提供自动补全选项。"""
    try:
        query = ctx.value.lower()
        # ctx.bot 让我们能访问到 CouponBot 实例及其缓存
        cached_projects: List[str] = getattr(ctx.bot, 'project_cache', [])
        
        if not query:
            return cached_projects[:25]

        return [p for p in cached_projects if query in p.lower()][:25]
    except Exception as e:
        logger.error(f"项目自动补全时出错: {e}")
        return []


from datetime import datetime, timezone
from typing import Optional
from constants import SUCCESS_COLOR, ERROR_COLOR, INFO_COLOR, WARN_COLOR

def format_relative_expiry(expiry_date: Optional[datetime]) -> str:
    """将有效期 datetime 对象格式化为用户友好的相对时间字符串。"""
    if expiry_date is None:
        return "永久有效"
    
    now = datetime.now(timezone.utc)
    # 确保 expiry_date 也是时区感知的
    if expiry_date.tzinfo is None:
        expiry_date = expiry_date.replace(tzinfo=timezone.utc)

    time_diff = expiry_date - now
    
    if time_diff.total_seconds() <= 0:
        return "已过期"
        
    days = time_diff.days
    hours, remainder = divmod(time_diff.seconds, 3600)
    
    if days > 0:
        return f"剩余约 {days} 天 {hours} 小时"
    elif hours > 0:
        return f"剩余约 {hours} 小时"
    else:
        minutes = remainder // 60
        return f"剩余约 {minutes} 分钟"

class EmbedFactory:
    """一个用于创建标准化 Embed 的工厂类"""
    
    @staticmethod
    def _create_base_embed(title: str, description: str, color: discord.Color) -> discord.Embed:
        """创建一个包含通用页脚和时间戳的基础 Embed"""
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.now(timezone.utc)
        )
        # 你可以在这里设置统一的页脚，例如
        # embed.set_footer(text="兑换券机器人")
        return embed

    @classmethod
    def success(cls, title: str, description: str) -> discord.Embed:
        """创建一个表示成功的 Embed"""
        return cls._create_base_embed(f"✅ {title}", description, SUCCESS_COLOR)

    @classmethod
    def error(cls, title: str, description: str) -> discord.Embed:
        """创建一个表示错误的 Embed"""
        return cls._create_base_embed(f"❌ {title}", description, ERROR_COLOR)

    @classmethod
    def info(cls, title: str, description: str) -> discord.Embed:
        """创建一个提供信息的 Embed"""
        return cls._create_base_embed(f"ℹ️ {title}", description, INFO_COLOR)

    @classmethod
    def warning(cls, title: str, description: str) -> discord.Embed:
        """创建一个表示警告的 Embed"""
        return cls._create_base_embed(f"⚠️ {title}", description, WARN_COLOR)
