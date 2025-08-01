import discord
from discord.ext import commands
from discord.commands import Option
from utils import project_autocompleter, EmbedFactory
import logging
from datetime import datetime, timezone
from typing import Optional
import asyncio
from utils import format_relative_expiry # <<< 从 utils 导入
from constants import (
    CLAIM_STATUS_SUCCESS, CLAIM_STATUS_BANNED, CLAIM_STATUS_DISABLED,
    CLAIM_STATUS_COOLDOWN, CLAIM_STATUS_NO_STOCK, CLAIM_STATUS_NO_PROJECT,
    CLAIM_STATUS_ERROR
)

logger = logging.getLogger('cog.user')

# --- Cog Class ---
class User(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _process_claim(self, interaction: discord.Interaction, project: str) -> bool:
        """
        处理申领请求的核心逻辑。
        :return: 如果申领成功则返回 True，否则返回 False。
        """
        user_id = interaction.user.id
        status, data = await self.bot.db_manager.claim_coupon(user_id, project)

        if status == CLAIM_STATUS_SUCCESS:
            self.bot.invalidate_stock_cache(project)
            coupon_code, expiry_date = data
            expiry_info = format_relative_expiry(expiry_date)
            
            description = f"这是您的专属兑换券代码 (仅您可见)：\n\n`{coupon_code}`\n\n**有效期**: {expiry_info}"
            embed = EmbedFactory.success(f"您在项目 '{project}' 的兑换券！", description)
            embed.set_footer(text=f"申领自: {interaction.guild.name}")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return True

        elif status == CLAIM_STATUS_BANNED:
            await interaction.followup.send(f"🚫 {data}", ephemeral=True)
        elif status == CLAIM_STATUS_DISABLED:
            await interaction.followup.send(f"抱歉，项目 **{project}** 的申领功能当前已禁用。", ephemeral=True)
        elif status == CLAIM_STATUS_COOLDOWN:
            cooldown_time, last_code = data
            last_coupon = await self.bot.db_manager.get_coupon_details(last_code)
            last_expiry_info = format_relative_expiry(last_coupon.expiry_date)

            description = f"您在项目 **{project}** 的申领正在冷却中。\n请在 **{cooldown_time}** 后再试。"
            embed = EmbedFactory.warning("申领正在冷却中", description)
            embed.add_field(name="您上次领取的兑换券", value=f"`{last_code}`", inline=False)
            embed.add_field(name="该券状态", value=last_expiry_info, inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        elif status == CLAIM_STATUS_NO_STOCK:
            await interaction.followup.send(f"抱歉，项目 **{project}** 的所有兑换券都已被申领完毕。", ephemeral=True)
        elif status == CLAIM_STATUS_NO_PROJECT:
            await interaction.followup.send(f"❌ 未找到项目 '{project}'。", ephemeral=True)
        elif status == CLAIM_STATUS_ERROR:
            logger.error(f"申领命令 '{project}' 时数据库返回了错误: {data}")
            await interaction.followup.send("处理您的请求时发生了一个内部错误，管理员已收到通知。", ephemeral=True)
        
        return False # 对于所有失败情况返回 False

    async def _process_my_claims(self, interaction: discord.Interaction):
        """处理“我的领取记录”请求的核心逻辑。"""
        await interaction.response.defer(ephemeral=True)
        
        claims = await self.bot.db_manager.get_claims_by_user(interaction.user.id, limit=3)
        
        if not claims:
            embed = EmbedFactory.info("领取记录", "您还没有任何兑换券领取记录。")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        description = "以下是您最近的3条领取记录：\n\n"
        for claim in claims:
            # 使用 discord.utils.format_dt 来创建动态更新的时间戳
            claimed_time_str = discord.utils.format_dt(claim.claimed_at, style='R')
            project_name = claim.project.name if claim.project else "未知项目"
            description += f"- **{project_name}**: `{claim.code}` (领取于 {claimed_time_str})\n"
            
        embed = EmbedFactory.info(f"{interaction.user.display_name} 的领取记录", description)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _show_inventory(self, interaction: discord.Interaction):
        """处理“查看库存”请求的核心逻辑。"""
        await interaction.response.defer(ephemeral=True)
        
        projects = self.bot.project_cache
        if not projects:
            embed = EmbedFactory.info("库存信息", "当前没有任何可用的项目。")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        embed = EmbedFactory.info("兑换券库存", "以下是所有项目的当前库存：")
        
        # 为了避免消息过长，可以分批发送或使用分页
        # 这里为了简单起见，我们一次性发送，但对项目数量做了限制
        description_parts = []
        for project_name in projects[:25]: # 最多显示25个项目
            stock = await self.bot.get_cached_stock(project_name)
            description_parts.append(f"🔹 **{project_name}**: `{stock if stock is not None else '查询失败'}` 张")
        
        embed.description = "\n".join(description_parts)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # 全局错误处理器更优，此处不再需要独立的错误监听器
    # on_application_command_error 已被移除，以防止重复响应

def setup(bot):
    bot.add_cog(User(bot))