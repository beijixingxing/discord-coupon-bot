import discord
import os
import logging
import traceback
import time
import yaml  # 导入 PyYAML
from datetime import datetime
from discord.ext import tasks, commands
from database import DatabaseManager
from typing import List, Set, Dict, Tuple, Any

logger = logging.getLogger('bot')


class CouponBot(discord.Bot):
    def __init__(self, *args, **kwargs):
        # 从 kwargs 中提取我们自己的参数，然后将其余的传递给父类
        self.admin_user_ids: Set[int] = kwargs.pop('admin_user_ids', set())
        
        super().__init__(*args, **kwargs)

        self.trusted_guilds: Set[int] = set(self.debug_guilds) if self.debug_guilds else set()
        self.db_manager = DatabaseManager()
        self.project_cache: List[str] = []
        self.stock_cache: Dict[str, Tuple[int, float]] = {}  # 项目名 -> (库存, 时间戳)

        self.load_cogs()

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
                except discord.errors.ExtensionNotFound:
                    logger.error(f"❌ 模块 '{name}' 加载失败：未找到 cogs/{name}.py 文件。")
                except discord.errors.ExtensionFailed as e:
                    # 这个异常会捕获模块内部的错误，比如像您遇到的 ModuleNotFoundError
                    logger.error(f"❌ 模块 '{name}' 加载失败，模块内部发生错误: {e}", exc_info=True)
                except Exception as e:
                    # 保留一个通用的异常捕获以应对未知情况
                    logger.error(f"❌ 模块 '{name}' 加载时发生未知错误: {e}", exc_info=True)
            else:
                logger.info(f"⚪️ 模块 '{name}' 在配置中被禁用，已跳过。")
        logger.info("--- 模块加载完成 ---")

    async def on_error(self, event_method: str, *args, **kwargs):
        logger.error(f"发生未处理的全局错误 (事件: {event_method}):\n{traceback.format_exc()}")

    async def on_application_command_error(self, ctx: discord.ApplicationContext, error: discord.DiscordException):
        """全局处理所有应用命令的错误。"""
        # 关键步骤：首先解包被包装的原始错误
        # ApplicationCommandInvokeError 是 CommandInvokeError 的子类
        if isinstance(error, commands.errors.CommandInvokeError):
            original_error = error.original
        else:
            original_error = error

        # 现在，使用解包后的原始错误进行判断
        if isinstance(original_error, discord.errors.NotFound) and original_error.code == 10062:
            logger.debug(f"交互 '{ctx.command.qualified_name}' 已被用户取消或超时，已忽略。")
            return # 直接返回，不再执行后续的错误记录和用户提示

        # 如果错误已经在特定Cog的处理器中处理过，则不再执行
        if ctx.command and ctx.command.has_error_handler():
            return

        # 记录所有其他未被处理的未知错误
        logger.error(
            f"命令 '{ctx.command.qualified_name if ctx.command else '未知命令'}' "
            f"发生未捕获的错误: {error}", # 记录原始的、完整的错误
            exc_info=error # 传递完整的异常信息以便追踪
        )
    
        # 向用户发送一个统一的、临时的错误消息
        error_message = "❌ 执行此命令时发生了一个未知的内部错误。管理员已收到通知。"
        try:
            if not ctx.interaction.response.is_done():
                await ctx.respond(error_message, ephemeral=True)
            else:
                # 如果已经响应过（例如，defer成功了但后续代码失败），则使用 followup
                await ctx.followup.send(error_message, ephemeral=True)
        except discord.errors.NotFound:
            # 如果此时交互也失效了，就放弃发送消息
            pass
        except Exception as e:
            logger.error(f"在向用户报告错误时，又发生了新的错误: {e}", exc_info=True)
          
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
      
        # 确保数据库已连接且表已创建
        await self.db_manager.connect()
      
        logger.info("正在启动所有后台任务...")
        self.update_project_cache.start() # <--- 在此启动
        self.auto_backup.start()
        self.cleanup_expired_coupons.start()
        logger.info("所有后台任务已成功启动。")

    @tasks.loop(hours=1)
    async def auto_backup(self):
        """每小时检查是否需要备份"""
        if datetime.utcnow().hour == 3:  # 每天凌晨3点执行
            success = await self.db_manager.backup_database()
            logger.info(f"自动备份{'成功' if success else '失败'}")

    @auto_backup.error
    async def on_backup_error(self, error):
        logger.error(f"备份任务出错: {error}")

    @tasks.loop(hours=1)
    async def cleanup_expired_coupons(self):
        """每小时清理一次过期兑换券"""
        try:
            count = await self.db_manager.cleanup_expired_coupons()
            if count > 0:
                logger.info(f"已清理 {count} 张过期兑换券")
                self.stock_cache.clear() # 清理所有缓存
        except Exception as e:
            logger.error(f"清理过期兑换券失败: {e}")

    @cleanup_expired_coupons.before_loop
    async def before_cleanup(self):
        await self.wait_until_ready()

    # --- 缓存控制 ---
    async def get_cached_stock(self, project_name: str, cache_duration: int = 60) -> int:
        """获取项目库存，优先使用缓存。"""
        current_time = time.time()
        if project_name in self.stock_cache:
            stock, timestamp = self.stock_cache[project_name]
            if current_time - timestamp < cache_duration:
                logger.debug(f"库存缓存命中: {project_name}")
                return stock

        logger.debug(f"库存缓存未命中或已过期: {project_name}")
        stock = await self.db_manager.get_stock(project_name)
        if stock is not None:
            self.stock_cache[project_name] = (stock, current_time)
        return stock

    def invalidate_stock_cache(self, project_name: str):
        """使指定项目的库存缓存失效。"""
        if project_name in self.stock_cache:
            del self.stock_cache[project_name]
            logger.info(f"项目 '{project_name}' 的库存缓存已失效。")