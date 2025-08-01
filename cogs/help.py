import discord
from discord.ext import commands
from utils import EmbedFactory

class Help(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    def _create_help_embed(self, is_admin: bool) -> discord.Embed:
        """创建一个帮助信息的 Embed 对象，适配UI面板交互。"""
        embed = EmbedFactory.info(
            "🤖 机器人使用指南",
            "我已经升级啦！现在所有核心功能都已集成至一个方便的 **UI控制面板** 中。"
        )
        
        embed.add_field(
            name="✨ 如何使用？",
            value="非常简单！只需要在任意授权频道使用斜杠命令 `/公益站`，我就会为您发送一个专属的控制面板，所有操作都可以通过点击上面的按钮完成。",
            inline=False
        )
        
        embed.add_field(
            name="面板功能一览",
            value=(
                "🔹 **查看库存**: 查询所有项目的兑换券数量。\n"
                "🔹 **申领兑换券**: 从可用项目中领取一张兑换券。\n"
                "🔹 **我的记录**: 查看您最近的申领历史。\n"
                "🔹 **捐赠密钥**: 为公益站贡献您宝贵的API密钥。\n"
                "🔹 **获取帮助**: 显示本帮助信息。"
            ),
            inline=False
        )

        if is_admin:
            embed.add_field(
                name="👑 管理员专属",
                value=(
                    "您可以通过主控制面板上的 **[⚙️ 管理面板]** 按钮访问管理员功能，包括项目管理、用户管理和系统操作等。\n"
                    "此外，一些独立的文件上传命令仍然可用，例如 `/管理 添加兑换券`。"
                ),
                inline=False
            )
            
        embed.set_footer(text="提示：大部分旧的斜杠命令已被UI面板替代。")
        return embed

    @commands.slash_command(name="帮助", description="显示机器人所有可用指令的帮助信息。")
    async def help_command(self, ctx: discord.ApplicationContext):
        """处理 /帮助 斜杠命令。"""
        # ephemeral=False 意味着帮助信息对所有人可见
        # 但由于 autoreply 模块会调用它，我们让调用者决定是否公开
        # 默认设为 True，保持私密
        await ctx.defer(ephemeral=True)
        is_admin = await self.bot.is_admin(ctx.author)
        embed = self._create_help_embed(is_admin)
        await ctx.followup.send(embed=embed, ephemeral=True)

def setup(bot):
    bot.add_cog(Help(bot))