import asyncio
import discord
from discord.ext import commands
from discord.commands import Option
from bot import project_autocompleter
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger('cog.user')

def _format_relative_expiry(expiry_date: Optional[datetime]) -> str:
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

# --- Cog Class ---
class User(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(name="库存", description="查询一个项目的可用(未过期)兑换券数量。(10秒后自动删除)")
    async def stock(self, ctx, project: Option(str, "要查询库存的项目。", autocomplete=project_autocompleter)): # <<< 已修正
        
        await ctx.defer() # 公开可见
        
        count = await self.bot.db_manager.get_stock(project)
        if count is None:
            msg = await ctx.followup.send(f"❌ 未找到项目 '{project}'。") # 公开错误消息
            await asyncio.sleep(10)
            try:
                await msg.delete()
            except Exception as e:
                logger.error(f"删除库存错误消息失败: {str(e)}")
            return

        embed = discord.Embed(
            title=f"🎟️ 项目 '{project}' 的兑换券库存",
            description=f"当前有 **{count}** 张兑换券可供申领。",
            color=discord.Color.blue()
        )
        msg = await ctx.followup.send(embed=embed)
        await asyncio.sleep(10)
        try:
            await msg.delete()
        except discord.NotFound:
            logger.debug("用户已手动删除库存消息")
        except discord.Forbidden:
            logger.error("机器人权限不足，无法删除库存消息")
        except Exception as e:
            logger.error(f"删除库存消息失败: {str(e)}")

    @commands.slash_command(name="申领", description="从一个指定的项目申领兑换券。")
    async def claim(self, ctx,
                  project: Option(str, "要申领兑换券的项目。", autocomplete=project_autocompleter)): # <<< 已修正
        
        await ctx.defer(ephemeral=True)
        user_id = ctx.author.id

        status, data = await self.bot.db_manager.claim_coupon(user_id, project)

        if status == 'SUCCESS':
            coupon_code = data
            coupon = await self.bot.db_manager.get_coupon_details(coupon_code)
            expiry_info = _format_relative_expiry(coupon.expiry_date)
            
            embed = discord.Embed(
                title=f"🎉 您在项目 '{project}' 的兑换券！",
                description=f"这是您的专属兑换券代码 (仅您可见)：\n\n**`{coupon_code}`**\n\n**有效期**: {expiry_info}",
                color=discord.Color.green()
            )
            embed.set_footer(text=f"申领自: {ctx.guild.name}")
            await ctx.followup.send(embed=embed)

        elif status == 'BANNED':
            await ctx.followup.send(f"🚫 {data}", ephemeral=True)
        elif status == 'DISABLED':
            await ctx.followup.send(f"抱歉，项目 **{project}** 的申领功能当前已禁用。", ephemeral=True)
        elif status == 'COOLDOWN':
            cooldown_time, last_code = data
            last_coupon = await self.bot.db_manager.get_coupon_details(last_code)
            last_expiry_info = _format_relative_expiry(last_coupon.expiry_date)

            embed = discord.Embed(
                title=f"⏳ 申领正在冷却中",
                description=f"您在项目 **{project}** 的申领正在冷却中。\n请在 **{cooldown_time}** 后再试。",
                color=discord.Color.orange()
            )
            embed.add_field(name="您上次领取的兑换券", value=f"`{last_code}`", inline=False)
            embed.add_field(name="该券状态", value=last_expiry_info, inline=False)
            await ctx.followup.send(embed=embed)
        elif status == 'NO_STOCK':
            await ctx.followup.send(f"抱歉，项目 **{project}** 的所有兑换券都已被申领完毕。", ephemeral=True)
        elif status == 'NO_PROJECT':
            await ctx.followup.send(f"❌ 未找到项目 '{project}'。", ephemeral=True)
        elif status == 'ERROR':
            logger.error(f"处理项目'{project}'的申领命令时数据库错误: {str(data)}")
            await ctx.followup.send("处理您的请求时发生了一个内部错误，管理员已收到通知。", ephemeral=True)

    # 全局错误处理器更优，此处不再需要独立的错误监听器
    # on_application_command_error 已被移除，以防止重复响应

def setup(bot):
    bot.add_cog(User(bot))
