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

def is_admin():
    """一个自定义检查函数，用于验证命令使用者是否为机器人管理员。"""
    def predicate(ctx: discord.ApplicationContext) -> bool:
        # 从 bot 实例中获取在启动时加载的管理员ID列表
        admin_ids = getattr(ctx.bot, 'admin_user_ids', set())
        return ctx.author.id in admin_ids
    return commands.check(predicate)
