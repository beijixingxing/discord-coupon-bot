import discord
from discord.ext import commands
import time
import os
import logging
import openai
from openai import AsyncOpenAI
import psutil
import asyncio

logger = logging.getLogger('cog.status')

API_CONFIG_KEYS = ['OPENAI_API_BASE', 'OPENAI_API_KEY', 'OPENAI_MODEL_NAME']

class Status(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.version = "2.0.0" # Merged version
    
        self.api_config = {key: os.getenv(key) for key in API_CONFIG_KEYS}
        self.is_api_configured = all(self.api_config.values())

        if self.is_api_configured:
            try:
                # 移除 proxies 参数，因为它在当前版本的 openai 库中不受支持
                self.openai_client = AsyncOpenAI(
                    api_key=self.api_config['OPENAI_API_KEY'],
                    base_url=self.api_config['OPENAI_API_BASE']
                )
                logger.info("公益站 API 客户端已成功初始化。")
            except Exception as e:
                logger.error(f"初始化 API 客户端失败: {str(e)}")
                self.openai_client = None
                self.is_api_configured = False
        else:
            self.openai_client = None
            logger.warning("未提供完整的【公益站 API】配置，相关状态检查将被跳过。")
        
        self.process = psutil.Process(os.getpid())

    async def _create_status_embed(self) -> discord.Embed:
        """创建并返回一个包含当前状态的 Embed 对象。"""
        # 并行获取状态数据
        bot_stats = {
            'latency': f"{self.bot.latency * 1000:.2f} ms",
            'cpu': f"{self.process.cpu_percent():.1f}%",
            'memory': f"{self.process.memory_info().rss / (1024 * 1024):.1f} MB"
        }
        
        api_info = await self.get_detailed_api_status()
        
        embed = discord.Embed(
            title="📊 机器人运行状态",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        
        system_cpu = f"{psutil.cpu_percent():.2f}%"
        system_mem = psutil.virtual_memory()
        system_mem_usage = f"{system_mem.percent:.2f}% ({system_mem.used/1024/1024/1024:.2f}/{system_mem.total/1024/1024/1024:.2f} GB)"
        disk = psutil.disk_usage('/')
        disk_usage = f"{disk.percent:.2f}% ({disk.used/1024/1024/1024:.2f}/{disk.total/1024/1024/1024:.2f} GB)"

        embed.add_field(
            name="⚙️ 机器人核心状态",
            value=(
                f"Discord 网络延迟: {bot_stats['latency']}\n"
                f"CPU 使用率: {bot_stats['cpu']}\n"
                f"内存占用: {bot_stats['memory']}"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🖥️ 服务器硬件状态",
            value=(
                f"系统 CPU 使用率: {system_cpu}\n"
                f"系统内存占用: {system_mem_usage}\n"
                f"硬盘使用率: {disk_usage}"
            ),
            inline=False
        )
        
        api_value = (
            f"状态: {api_info['status']}\n"
            f"端点: {self.api_config['OPENAI_API_BASE'] or '未配置'}\n"
        )
        if api_info.get('latency_ms') is not None:
            api_value += f"延迟: {api_info['latency_ms']:.2f} ms\n"
        if self.api_config['OPENAI_MODEL_NAME']:
            api_value += f"使用模型: '{self.api_config['OPENAI_MODEL_NAME']}'\n"
        if api_info.get('error'):
            api_value += f"详情: {api_info['error']}"

        embed.add_field(
            name="🔗 公益站 API 状态",
            value=api_value,
            inline=False
        )
        
        embed.set_footer(text=f"版本 {self.version} • 状态报告 • {time.strftime('%Y-%m-%d %H:%M', time.localtime())}")
        return embed

    @commands.slash_command(name="状态", description="查看机器人和数据库状态(10秒后自动删除)")
    async def public_status(self, ctx: discord.ApplicationContext):
        """综合状态面板"""
        await ctx.defer()
        
        embed = await self._create_status_embed()
        msg = await ctx.followup.send(embed=embed)
        
        delay = self.bot.config.get('ux', {}).get('message_delete_delay', 10)
        if delay > 0:
            await asyncio.sleep(delay)
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

    async def get_detailed_api_status(self) -> dict:
        if not self.is_api_configured or not self.openai_client:
            return {"status": "⚠️ 未配置"}
    
        start_time = time.time()
        try:
            await self.openai_client.models.list(timeout=10)
            latency = (time.time() - start_time) * 1000
            return {
                "status": "✅ 连接正常",
                "latency_ms": latency,
            }
        except Exception as e:
            logger.error(f"API检查错误: {str(e)}")
            return {
                "status": "❌ 连接失败",
                "error": str(e)
            }

def setup(bot):
    bot.add_cog(Status(bot))