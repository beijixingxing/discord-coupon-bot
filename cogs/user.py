import discord
from discord.ext import commands
from discord.commands import Option
from bot import project_autocompleter # <<< 已修正
import logging

logger = logging.getLogger('cog.user')

# --- Cog Class ---
class User(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(name="库存", description="查询一个项目的可用兑换券数量。")
    async def stock(self, ctx, project: Option(str, "要查询库存的项目。", autocomplete=project_autocompleter)): # <<< 已修正
        
        await ctx.defer(ephemeral=False) # 让所有用户都能看到库存
        
        count = await self.bot.db_manager.get_stock(project)
        if count is None:
            await ctx.followup.send(f"❌ 未找到项目 '{project}'。", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🎟️ 项目 '{project}' 的兑换券库存",
            description=f"当前有 **{count}** 张兑换券可供申领。",
            color=discord.Color.blue()
        )
        await ctx.followup.send(embed=embed)

    @commands.slash_command(name="申领", description="从一个指定的项目申领兑换券。")
    async def claim(self, ctx, project: Option(str, "要申领兑换券的项目。", autocomplete=project_autocompleter)): # <<< 已修正
        
        await ctx.defer(ephemeral=True)
        user_id = ctx.author.id

        status, data = await self.bot.db_manager.claim_coupon(user_id, project)

        if status == 'SUCCESS':
            coupon_code = data
            embed = discord.Embed(
                title=f"🎉 您在项目 '{project}' 的兑换券！",
                description=f"这是您的专属兑换券代码 (仅您可见)：\n\n**`{coupon_code}`**",
                color=discord.Color.green()
            )
            # 优化：由于 interaction_check 确保了命令总是在服务器中执行，
            # 因此 ctx.guild 不会为 None，可以直接使用。
            embed.set_footer(text=f"申领自: {ctx.guild.name}")
            await ctx.followup.send(embed=embed, ephemeral=True)

        elif status == 'BANNED':
            await ctx.followup.send(f"🚫 {data}", ephemeral=True)
        elif status == 'DISABLED':
            await ctx.followup.send(f"抱歉，项目 **{project}** 的申领功能当前已禁用。", ephemeral=True)
        elif status == 'COOLDOWN':
            cooldown_time, last_code = data
            embed = discord.Embed(
                title=f"⏳ 申领正在冷却中",
                description=f"您在项目 **{project}** 的申领正在冷却中。\n请在 **{cooldown_time}** 后再试。",
                color=discord.Color.orange()
            )
            embed.add_field(name="您上次领取的兑换券是", value=f"`{last_code}`", inline=False)
            await ctx.followup.send(embed=embed, ephemeral=True)
        elif status == 'NO_STOCK':
            await ctx.followup.send(f"抱歉，项目 **{project}** 的所有兑换券都已被申领完毕。", ephemeral=True)
        elif status == 'NO_PROJECT':
            await ctx.followup.send(f"❌ 未找到项目 '{project}'。", ephemeral=True)
        elif status == 'ERROR':
            logger.error(f"申领命令 '{project}' 时数据库返回了错误: {data}")
            await ctx.followup.send("处理您的请求时发生了一个内部错误，管理员已收到通知。", ephemeral=True)

    # 全局错误处理器更优，此处不再需要独立的错误监听器
    # on_application_command_error 已被移除，以防止重复响应

def setup(bot):
    bot.add_cog(User(bot))
