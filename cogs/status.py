import discord
from discord.ext import commands
import time
import os
import logging
import openai
from openai import AsyncOpenAI
import psutil
import asyncio
from config import Config

logger = logging.getLogger('cog.status')

API_CONFIG_KEYS = ['OPENAI_API_BASE', 'OPENAI_API_KEY', 'OPENAI_MODEL_NAME']

class Status(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.version = Config.VERSION
    
        self.api_config = {key: os.getenv(key) for key in API_CONFIG_KEYS}
        self.is_api_configured = all(self.api_config.values())

        if self.is_api_configured:
            try:
                self.openai_client = AsyncOpenAI(
                    api_key=self.api_config['OPENAI_API_KEY'],
                    base_url=self.api_config['OPENAI_API_BASE'],
                )
                logger.info("星星人民公益站 API 客户端已成功初始化。")
            except Exception as e:
                logger.error(f"初始化 API 客户端失败: {str(e)}")
                self.openai_client = None
                self.is_api_configured = False
        else:
            self.openai_client = None
            logger.warning("未提供完整的【星星人民公益站 API】配置，相关状态检查将被跳过。")
        
        self.process = psutil.Process(os.getpid())

    async def check_backup_status(self):
        """检查数据库备份状态"""
        backup_dir = os.path.join(os.path.dirname(__file__), '../../backups')
        if not os.path.exists(backup_dir):
            return "⚠️ 未配置", "未找到备份目录"
            
        backups = sorted(
            [f for f in os.listdir(backup_dir) if f.endswith('.db')],
            key=lambda f: os.path.getmtime(os.path.join(backup_dir, f)),
            reverse=True
        )
        
        if not backups:
            return "⚠️ 无备份", "未找到备份文件"
            
        latest_backup = backups[0]
        latest_time = os.path.getmtime(os.path.join(backup_dir, latest_backup))
        return (
            "✅ 正常备份",
            f"最新备份: {time.strftime('%Y-%m-%d %H:%M', time.localtime(latest_time))}\n"
            f"备份数量: {len(backups)}/10"
        )

    @commands.slash_command(name="状态", description="查看机器人和数据库状态(10秒后自动删除)")
    async def public_status(self, ctx: discord.ApplicationContext):
        """综合状态面板"""
        await ctx.defer()
        
        # 并行获取状态数据
        bot_stats = {
            'latency': f"{self.bot.latency * 1000:.2f} ms",
            'cpu': f"{self.process.cpu_percent():.1f}%",
            'memory': f"{self.process.memory_info().rss / (1024 * 1024):.1f} MB"
        }
        
        # 获取API和备份状态
        api_status, api_details = await self.get_detailed_api_status()
        backup_status, backup_details = await self.check_backup_status()
        
        # 创建状态面板
        embed = discord.Embed(
            title="📊 机器人运行状态",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        
        # 获取系统硬件状态
        system_cpu = f"{psutil.cpu_percent():.2f}%"
        system_mem = psutil.virtual_memory()
        system_mem_usage = f"{system_mem.percent:.2f}% ({system_mem.used/1024/1024/1024:.2f}/{system_mem.total/1024/1024/1024:.2f} GB)"
        disk = psutil.disk_usage('/')
        disk_usage = f"{disk.percent:.2f}% ({disk.used/1024/1024/1024:.2f}/{disk.total/1024/1024/1024:.2f} GB)"

        # 机器人核心状态
        embed.add_field(
            name="⚙️ 机器人核心状态",
            value=(
                f"Discord 网络延迟: {bot_stats['latency']}\n"
                f"CPU 使用率: {bot_stats['cpu']}\n"
                f"内存占用: {bot_stats['memory']}"
            ),
            inline=False
        )
        
        # 服务器硬件状态
        embed.add_field(
            name="🖥️ 服务器硬件状态",
            value=(
                f"系统 CPU 使用率: {system_cpu}\n"
                f"系统内存占用: {system_mem_usage}\n"
                f"硬盘使用率: {disk_usage}"
            ),
            inline=False
        )
        
        # API状态
        embed.add_field(
            name="🔗 星星人民公益站 API 状态",
            value=(
                f"状态: {api_status}\n"
                f"端点: {self.api_config['OPENAI_API_BASE']}\n"
                f"延迟: {api_details.split('延迟: ')[1].split(' ms')[0]} ms\n"
                f"使用模型: '{self.api_config['OPENAI_MODEL_NAME']}'"
            ),
            inline=False
        )
        
        embed.set_footer(text=f"版本 {self.version} • 状态报告 • {time.strftime('%Y-%m-%d %H:%M', time.localtime())}")
        
        # 发送并设置自动删除
        msg = await ctx.followup.send(embed=embed)
        await asyncio.sleep(10)
        try:
            await msg.delete()
        except discord.NotFound:
            logger.debug("状态消息已被手动删除")
        except discord.Forbidden:
            logger.error("删除消息权限不足，请确保机器人有'管理消息'权限")
        except discord.HTTPException as e:
            logger.error(f"Discord API错误: {e.status} {e.text}", exc_info=True)
        except Exception as e:
            logger.error(f"删除状态消息失败: {str(e)}")

    async def get_detailed_api_status(self):
        if not self.is_api_configured or not self.openai_client:
            return "⚠️ 未配置", "未在 `.env` 文件中提供完整的API配置"
      
        start_time = time.time()
        try:
            await self.openai_client.models.list(timeout=10)
            latency = (time.time() - start_time) * 1000
            status_text = "✅ 连接正常"
            details = f"• 端点: {self.api_config['OPENAI_API_BASE']}\n" \
                     f"• 延迟: {latency:.2f} ms\n" \
                     f"• 模型: {self.api_config['OPENAI_MODEL_NAME']}"
            return status_text, details
        except Exception as e:
            logger.error(f"API检查错误: {str(e)}")
            return "❌ 连接失败", f"错误: {str(e)}"

def setup(bot):
    bot.add_cog(Status(bot))
