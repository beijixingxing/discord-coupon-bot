import discord
import os
import logging
import traceback
from discord.ext import tasks, commands
from database import DatabaseManager
from typing import List, Set

logger = logging.getLogger('bot')


async def project_autocompleter(ctx: discord.AutocompleteContext) -> List[str]:
    try:
        query = ctx.value.lower()
        cached_projects: List[str] = ctx.bot.project_cache
        
        if not query:
            return cached_projects[:25]

        return [p for p in cached_projects if query in p.lower()][:25]
    except Exception as e:
        logger.error(f"项目自动补全时出错: {e}")
        return []

class CouponBot(discord.Bot):
    def __init__(self, *args, **kwargs):
        # 首先，调用父类的构造函数，让它处理所有 discord.py 相关的初始化。
        # main.py 传递的 `debug_guilds` 会被正确处理。
        super().__init__(*args, **kwargs)

        # 在父类初始化后，`self.debug_guilds` 属性就会被设置。
        # 我们用它来初始化我们自己的受信任服务器列表，这比手动解析 kwargs 更健壮。
        self.trusted_guilds: Set[int] = set(self.debug_guilds) if self.debug_guilds else set()

        self.db_manager = DatabaseManager()
        self.project_cache: List[str] = []

        self.load_cogs()
        self.update_project_cache.start()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """全局检查所有交互是否来自可信服务器。"""
        # 如果在私信中调用，则 interaction.guild 为 None，直接阻止
        if not interaction.guild:
            await interaction.response.send_message("❌ 此命令无法在私信中使用。", ephemeral=True)
            return False

        # 如果 self.trusted_guilds 列表被配置（不为空），则进行检查
        if self.trusted_guilds:
            if interaction.guild.id not in self.trusted_guilds:
                # 服务器未授权，记录警告并回复用户
                command_name = "未知命令"
                if interaction.command:
                    command_name = interaction.command.qualified_name
              
                logger.warning(
                    f"已阻止来自未授权服务器 {interaction.guild.id} ({interaction.guild.name}) "
                    f"的用户 {interaction.user.id} ({interaction.user.name}) "
                    f"执行命令 '{command_name}'。"
                )
                await interaction.response.send_message("❌ 此机器人未被授权在此服务器上运行。", ephemeral=True)
                return False
      
        # 如果 self.trusted_guilds 为空（全局模式），或检查通过，则允许交互
        return True

    def load_cogs(self):
        cogs_dir = './cogs'
        if not os.path.isdir(cogs_dir):
            logger.warning(f"Cogs 目录 '{cogs_dir}' 未找到，将跳过加载。")
            return
            
        for filename in os.listdir(cogs_dir):
            if filename.endswith('.py') and not filename.startswith('__'):
                try:
                    self.load_extension(f'cogs.{filename[:-3]}')
                    logger.info(f'成功加载模块: {filename}')
                except Exception as e:
                    logger.error(f'加载模块 {filename} 失败: {e}', exc_info=True)

    def cog_unload(self):
        self.update_project_cache.cancel()

    async def on_error(self, event_method: str, *args, **kwargs):
        logger.error(f"发生未处理的全局错误 (事件: {event_method}):\n{traceback.format_exc()}")

    @tasks.loop(minutes=5)
    async def update_project_cache(self):
        try:
            self.project_cache = await self.db_manager.get_all_project_names()
            logger.info(f"项目缓存已更新，共 {len(self.project_cache)} 个项目。")
        except Exception as e:
            logger.error(f"更新项目缓存失败: {e}", exc_info=True)
    
    @update_project_cache.before_loop
    async def before_update_cache(self):
        logger.info("等待机器人就绪以启动缓存循环...")
        await self.wait_until_ready()
        logger.info("机器人已就绪。缓存更新循环启动。")

    async def on_ready(self):
        if self.user:
            logger.info(f'以 {self.user} ({self.user.id}) 的身份登录成功')
        else:
            logger.info("机器人已登录，但用户信息不可用。")
        logger.info('------')
        await self.db_manager.connect()
        # 关键修复：`update_project_cache` 是一个异步任务，需要被 await
        await self.update_project_cache()
