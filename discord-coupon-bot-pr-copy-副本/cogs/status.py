# cogs/status.py - 修正后的代码

import discord
from discord.ext import commands, tasks
import time
import os
import logging
import openai
from openai import AsyncOpenAI
import psutil
import asyncio
import httpx  # 确保 httpx 已导入
import platform # 导入 platform 模块
import datetime
import sys

logger = logging.getLogger('cog.status')

API_CONFIG_KEYS = ['OPENAI_API_BASE', 'OPENAI_API_KEY', 'OPENAI_MODEL_NAME']

# --- Helper Function for Uptime ---
def format_uptime(seconds):
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    
    uptime_str = ""
    if d > 0:
        uptime_str += f"{int(d)}天 "
    if h > 0:
        uptime_str += f"{int(h)}小时 "
    if m > 0:
        uptime_str += f"{int(m)}分钟 "
    uptime_str += f"{int(s)}秒"
    
    return uptime_str.strip()

class Status(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.version = "2.0.0" # Merged version
        self.start_time = datetime.datetime.utcnow()
  
        self.api_config = {key: os.getenv(key) for key in API_CONFIG_KEYS}
        self.is_api_configured = all(self.api_config.values())
        self.openai_client = None  # 初始设为 None

        if self.is_api_configured:
            try:
                # 1. 从环境变量获取代理 URL
                proxy_url = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
                http_client = None # 默认为 None

                # 2. 如果存在代理 URL，则创建并配置 httpx 客户端
                if proxy_url:
                    # httpx 的 proxies 参数需要一个字典
                    proxies = {"all://": proxy_url}
                    http_client = httpx.AsyncClient(proxies=proxies)
                    logger.info(f"检测到代理配置，将通过 {proxy_url} 初始化 API 客户端。")

                # 3. 将配置好的 http_client (或 None) 传递给 AsyncOpenAI
                self.openai_client = AsyncOpenAI(
                    api_key=self.api_config['OPENAI_API_KEY'],
                    base_url=self.api_config['OPENAI_API_BASE'],
                    http_client=http_client  # <--- 这是正确的参数
                )
              
                logger.info("公益站 API 客户端已成功初始化。")

            except Exception as e:
                # 捕获初始化过程中任何可能的错误
                logger.error(f"初始化 API 客户端时发生严重错误: {e}", exc_info=True)
                self.is_api_configured = False # 将配置状态设为 False
        else:
            logger.warning("未提供完整的【公益站 API】配置，将跳过相关状态检查。")
      
        self.process = psutil.Process(os.getpid())

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"'{self.__class__.__name__}' cog is ready!")
        self.update_status.start()

    async def get_openai_status(self):
        if not self.is_api_configured or not self.openai_client:
            return "N/A"
        
        try:
            start_time = time.time()
            # 使用 stream=False 进行简单调用
            chat_completion = await self.openai_client.chat.completions.create(
                messages=[{"role": "user", "content": "hi"}],
                model=self.api_config['OPENAI_MODEL_NAME'],
                max_tokens=1,
                stream=False, 
            )
            end_time = time.time()
            delay = (end_time - start_time) * 1000
            return f"正常 ({delay:.2f}ms)"
        except openai.APIConnectionError as e:
            logger.error(f"无法连接到公益站 API: {e}")
            return "连接失败"
        except openai.RateLimitError as e:
            logger.warning(f"公益站 API 请求被限速: {e}")
            return "请求限速"
        except openai.APIStatusError as e:
            logger.error(f"公益站 API 返回状态错误: status_code={e.status_code}, response={e.response}")
            return f"API 错误 (Code: {e.status_code})"
        except Exception as e:
            logger.error(f"检查公益站 API 状态时发生未知错误: {e}", exc_info=True)
            return "未知错误"

    @tasks.loop(seconds=60)
    async def update_status(self):
        """每60秒更新一次机器人的状态信息。"""
        try:
            latency = self.bot.latency * 1000
            guild_count = len(self.bot.guilds)
            user_count = len(self.bot.users)
            
            openai_status = await self.get_openai_status()

            status_message = (
                f"v{self.version} | "
                f"服务器: {guild_count} | "
                f"用户: {user_count} | "
                f"延迟: {latency:.2f}ms | "
                f"公益站: {openai_status}"
            )
            
            await self.bot.change_presence(activity=discord.Game(name=status_message))
        except Exception as e:
            logger.error(f"更新机器人状态任务出错: {e}", exc_info=True)

    @update_status.before_loop
    async def before_update_status(self):
        """在任务开始前，等待机器人完全就绪。"""
        await self.bot.wait_until_ready()

    @commands.slash_command(name="系统状态", description="显示机器人和服务器的详细状态信息")
    async def system_status(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)

        # --- 硬件 & 系统信息 ---
        cpu_usage = psutil.cpu_percent(interval=1)
        
        # --- 动态颜色 ---
        if cpu_usage > 80:
            embed_color = discord.Color.red()
        elif cpu_usage > 50:
            embed_color = discord.Color.yellow()
        else:
            embed_color = discord.Color.green()

        mem_info = psutil.virtual_memory()
        mem_usage_gb = mem_info.used / (1024**3)
        mem_total_gb = mem_info.total / (1024**3)
        disk_info = psutil.disk_usage('/')
        disk_usage_gb = disk_info.used / (1024**3)
        disk_total_gb = disk_info.total / (1024**3)
        
        sys_version = platform.platform()
        
        # 获取系统负载和CPU核心数
        try:
            # 使用物理核心数进行计算，如果获取失败则使用逻辑核心数
            cpu_cores = psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True)
            load_avg = psutil.getloadavg()
            # 计算1分钟负载的百分比
            load_percent = (load_avg[0] / cpu_cores) * 100
            load_str = f"{load_percent:.1f}% (1分钟平均)"
        except (AttributeError, ZeroDivisionError):
            load_str = "N/A"

        # --- 机器人信息 ---
        latency = self.bot.latency * 1000
        uptime_delta = datetime.datetime.utcnow() - self.start_time
        uptime_str = format_uptime(uptime_delta.total_seconds())
        python_version = platform.python_version()
        pycord_version = discord.__version__

        # --- 服务状态 ---
        openai_status = await self.get_openai_status()
        
        # --- 创建 Embed ---
        embed = discord.Embed(title="📊 系统状态", color=embed_color)
        
        embed.add_field(
            name="🖥️ 操作系统",
            value=f"`{sys_version}`",
            inline=False
        )

        embed.add_field(
            name="⚙️ 硬件状态",
            value=f"💻 **CPU:** `{cpu_usage:.1f}%`\n"
                  f"🧠 **内存:** `{mem_usage_gb:.2f} / {mem_total_gb:.2f} GB ({mem_info.percent}%)`\n"
                  f"💾 **硬盘:** `{disk_usage_gb:.2f} / {disk_total_gb:.2f} GB ({disk_info.percent}%)`\n"
                  f"📈 **负载:** `{load_str}`",
            inline=False
        )
        
        embed.add_field(
            name="🤖 机器人状态",
            value=f"⏳ **持续运行:** `{uptime_str}`\n"
                  f"🌐 **网络延迟:** `{latency:.2f}ms`\n"
                  f"ℹ️ **核心版本:** `Python {python_version}` | `Py-cord {pycord_version}`",
            inline=False
        )

        if self.is_api_configured:
            api_model = self.api_config.get('OPENAI_MODEL_NAME', '未设置')
            api_info_value = (
                f"**状态:** `{openai_status}`\n"
                f"**模型:** `{api_model}`"
            )
        else:
            api_info_value = "**状态:** `未配置`"

        embed.add_field(
            name="🔌 星星人民公益站 API",
            value=api_info_value,
            inline=False
        )

        embed.set_footer(text=f"由 {self.bot.user.name} 提供", icon_url=self.bot.user.avatar.url)
        embed.timestamp = discord.utils.utcnow()

        # 消息设为仅自己可见，不再需要自动删除
        await ctx.followup.send(embed=embed, ephemeral=True)

def setup(bot: discord.Bot):
    bot.add_cog(Status(bot))