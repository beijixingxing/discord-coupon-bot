import discord
import logging
import os
import traceback
from datetime import datetime
from discord.ext import tasks, commands
from database import DatabaseManager
from typing import List, Set
from config import Config

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
        self.trusted_guilds: Set[int] = set(Config.TRUSTED_GUILDS) if Config.TRUSTED_GUILDS else set()

        self.db_manager = DatabaseManager()
        self.project_cache: List[str] = []

        self.load_cogs()
        # self.update_project_cache.start() # <<< 移至 on_ready 中

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """全局检查所有交互是否来自可信服务器。"""
        # 允许状态/库存命令在任何服务器使用
        if interaction.command and interaction.command.name in ["状态", "库存"]:
            return True
            
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
                # 跳过不存在的admin_cog.py
                if filename == 'admin_cog.py':
                    continue
                try:
                    self.load_extension(f'cogs.{filename[:-3]}')
                    logger.info(f'成功加载模块: {filename}')
                except Exception as e:
                    logger.error(f'加载模块 {filename} 失败: {e}', exc_info=True)

    def cog_unload(self):
        self.update_project_cache.cancel()

    async def on_error(self, event_method: str, *args, **kwargs):
        logger.error(f"发生未处理的全局错误 (事件: {event_method}):\n{traceback.format_exc()}")

    @tasks.loop(hours=1)
    async def auto_backup(self):
        """每小时检查是否需要备份"""
        if datetime.utcnow().hour == 3:  # 每天凌晨3点执行
            success = await self.db_manager.backup_database()
            logger.info(f"自动备份{'成功' if success else '失败'}")

    @auto_backup.error
    async def on_backup_error(self, error):
        logger.error(f"备份任务出错: {error}")

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
        # 1. 记录机器人登录成功信息
        if self.user:
            logger.info(f'以 {self.user} ({self.user.id}) 的身份登录成功')
        else:
            logger.info("机器人已登录，但用户信息不可用。")
        logger.info('------')

        # 2. **最关键的一步**: 首先连接数据库并创建所有表结构
        logger.info("正在连接到数据库并初始化表结构...")
        await self.db_manager.connect()
        logger.info("数据库连接成功，表结构已同步。")

        # 3. 数据库就绪后，再启动所有依赖数据库的后台任务
        logger.info("正在启动后台任务...")
        self.auto_backup.start()
        self.cleanup_expired_coupons.start()
        self.update_project_cache.start()
        logger.info("所有后台任务已成功启动。")

    @tasks.loop(hours=1)
    async def cleanup_expired_coupons(self):
        """每小时清理一次过期兑换券"""
        try:
            count = await self.db_manager.cleanup_expired_coupons()
            if count > 0:
                logger.info(f"已清理 {count} 张过期兑换券")
        except Exception as e:
            logger.error(f"清理过期兑换券失败: {e}")

    @cleanup_expired_coupons.before_loop
    async def before_cleanup(self):
        await self.wait_until_ready()
