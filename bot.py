import discord
import os
import logging
import traceback
import yaml  # 导入 PyYAML
from discord.ext import tasks, commands
from database import DatabaseManager
from typing import List, Set

logger = logging.getLogger('bot')


class CouponBot(discord.Bot):
    def __init__(self, *args, **kwargs):
        # 从 kwargs 中提取我们自己的参数，然后将其余的传递给父类
        self.admin_user_ids: Set[int] = kwargs.pop('admin_user_ids', set())
        
        super().__init__(*args, **kwargs)

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
        """根据 config.yml 文件加载模块。"""
        config_path = 'config.yml'
        if not os.path.exists(config_path):
            logger.critical(f"配置文件 '{config_path}' 未找到。机器人无法加载任何模块。")
            return

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
        except (yaml.YAMLError, IOError) as e:
            logger.critical(f"读取或解析配置文件 '{config_path}' 失败: {e}", exc_info=True)
            return

        if 'cogs' not in config or not isinstance(config['cogs'], list):
            logger.warning(f"配置文件 '{config_path}' 格式不正确或缺少 'cogs' 列表。")
            return

        logger.info("--- 开始根据配置文件加载模块 ---")
        for cog_config in config['cogs']:
            name = cog_config.get('name')
            enabled = cog_config.get('enabled', False)

            if not name:
                logger.warning("在配置文件中发现一个没有 'name' 的模块条目，已跳过。")
                continue

            if enabled:
                try:
                    self.load_extension(f'cogs.{name}')
                    logger.info(f"✅ 模块 '{name}' 已成功加载。")
                except commands.ExtensionNotFound:
                    logger.error(f"❌ 模块 '{name}' 加载失败：未找到 cogs/{name}.py 文件。")
                except Exception as e:
                    logger.error(f"❌ 模块 '{name}' 加载失败: {e}", exc_info=True)
            else:
                logger.info(f"⚪️ 模块 '{name}' 在配置中被禁用，已跳过。")
        logger.info("--- 模块加载完成 ---")

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
        # 优化：移除多余的缓存更新调用。
        # update_project_cache 任务已在 __init__ 中通过 .start() 启动，
        # 其 before_loop 会确保在机器人就绪后自动运行第一次。
