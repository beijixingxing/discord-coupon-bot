import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup, Option
from utils import project_autocompleter, is_admin
from typing import Optional
import logging

logger = logging.getLogger('cog.admin')

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    admin = SlashCommandGroup(
        "管理",
        "兑换券机器人管理命令",
        checks=[is_admin()]  # 使用新的基于用户ID的检查
    )

    # --- Project Management Commands ---
    @admin.command(name="创建项目", description="创建一个新的兑换券项目。")
    async def create_project(self, ctx, name: Option(str, "新项目的名称。")):
        success, message = await self.bot.db_manager.create_project(name)
        if success:
            await ctx.respond(f"✅ {message}", ephemeral=True)
            await self.bot.update_project_cache() # 立即更新缓存
        else:
            await ctx.respond(f"❌ {message}", ephemeral=True)

    @admin.command(name="删除项目", description="永久删除一个项目及其所有数据（危险操作！）。")
    async def delete_project(self, ctx, project: Option(str, "要永久删除的项目。", autocomplete=project_autocompleter)): # <<< 已修正
      
        class ConfirmationView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=30.0)
                self.value = None

            @discord.ui.button(label="确认删除", style=discord.ButtonStyle.danger)
            async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
                # 优化：在处理前禁用所有按钮，防止重复点击
                for item in self.children:
                    item.disabled = True
                await interaction.response.edit_message(view=self)
                self.value = True
                self.stop()

            @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
            async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction):
                # 优化：在处理前禁用所有按钮
                for item in self.children:
                    item.disabled = True
                await interaction.response.edit_message(view=self)
                self.value = False
                self.stop()

        view = ConfirmationView()
        
        await ctx.respond(
            f"**⚠️ 警告：您确定要永久删除项目 `{project}` 吗？**\n"
            f"此操作不可逆，将同时删除该项目下**所有**的兑换券和封禁记录。",
            view=view,
            ephemeral=True
        )

        await view.wait()

        # 按钮已在回调中被禁用，这里只需要根据结果更新消息
        if view.value is True:
            success, message = await self.bot.db_manager.delete_project(project)
            if success:
                await self.bot.update_project_cache() # 立即更新缓存
                await ctx.edit(content=f"✅ {message}", view=None)
            else:
                await ctx.edit(content=f"❌ {message}", view=None)

        elif view.value is False:
            await ctx.edit(content="操作已取消。", view=None)
      
        else:
            await ctx.edit(content="操作超时，已自动取消。", view=None)

    # --- Coupon Management Commands ---
    @admin.command(name="添加兑换券", description="向指定项目批量添加兑换券。")
    async def add_coupons(self, ctx,
                          project: Option(str, "要添加兑换券的项目。", autocomplete=project_autocompleter), # <<< 已修正
                          file: Option(discord.Attachment, "包含兑换券代码的 .txt 文件。")):
        if not file.filename.endswith('.txt'):
            await ctx.respond("❌ 请上传一个有效的 `.txt` 文件。", ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        
        file_content = await file.read()
        codes = [code.strip() for code in file_content.decode('utf-8').splitlines() if code.strip()]

        if not codes:
            await ctx.followup.send("文件是空的或不包含有效的代码。", ephemeral=True)
            return

        result = await self.bot.db_manager.add_coupons(project, codes)
        if result is None:
            await ctx.followup.send(f"❌ 未找到项目 '{project}'。", ephemeral=True)
            return
        
        newly_added, duplicates = result
        await ctx.followup.send(
            f"✅ **文件已为项目 '{project}' 处理完毕！**\n"
            f"- 新增兑换券: **{newly_added}**\n"
            f"- 忽略的重复券: **{duplicates}**",
            ephemeral=True
        )

    # --- Settings Commands ---
    @admin.command(name="开关申领", description="为一个项目启用或禁用申领功能。")
    async def toggle_claim(self, ctx,
                           project: Option(str, "要修改的项目。", autocomplete=project_autocompleter), # <<< 已修正
                           status: Option(str, "新的申领状态。", choices=["开启", "关闭"])):
        new_status = True if status == '开启' else False
        success = await self.bot.db_manager.set_project_setting(project, 'is_claim_active', new_status)
        if success:
            await ctx.respond(f"✅ 项目 **{project}** 的申领功能已 **{status}**。", ephemeral=True)
        else:
            await ctx.respond(f"❌ 未找到项目 '{project}'。", ephemeral=True)

    @admin.command(name="设置冷却", description="为一个项目设置申领冷却时间。")
    async def set_cooldown(self, ctx,
                           project: Option(str, "要修改的项目。", autocomplete=project_autocompleter), # <<< 已修正
                           hours: Option(int, "冷却时间（小时）。", min_value=0)):
        success = await self.bot.db_manager.set_project_setting(project, 'claim_cooldown_hours', hours)
        if success:
            await ctx.respond(f"✅ 项目 **{project}** 的冷却时间已设置为 **{hours}** 小时。", ephemeral=True)
        else:
            await ctx.respond(f"❌ 未找到项目 '{project}'。", ephemeral=True)

    # --- User Moderation Commands ---
    @admin.command(name="封禁", description="禁止一个用户申领兑换券。")
    async def ban(self, ctx,
                  user: Option(discord.Member, "要封禁的用户。"),
                  reason: Option(str, "封禁的原因。"),
                  project: Option(str, "要封禁的项目（留空则为全局封禁）。", autocomplete=project_autocompleter, required=False), # <<< 已修正
                  duration_hours: Option(int, "封禁时长（小时，留空则为永久）。", min_value=1, required=False)):
        
        success, message = await self.bot.db_manager.ban_user(user.id, project, reason, duration_hours)
        if success:
            await ctx.respond(f"✅ **{user.display_name}** 已被封禁。{message}", ephemeral=True)
        else:
            await ctx.respond(f"❌ 封禁失败。{message}", ephemeral=True)

    @admin.command(name="解封", description="解除用户的封禁。")
    async def unban(self, ctx,
                    user: Option(discord.Member, "要解封的用户。"),
                    project: Option(str, "要解封的项目（留空则为全局）。", autocomplete=project_autocompleter, required=False)): # <<< 已修正
        
        success, message = await self.bot.db_manager.unban_user(user.id, project)
        if success:
            await ctx.respond(f"✅ **{user.display_name}** 已被解封。{message}", ephemeral=True)
        else:
            await ctx.respond(f"❌ 解封失败。{message}", ephemeral=True)

    # --- Error Handler ---
    @commands.Cog.listener()
    async def on_application_command_error(self, ctx: discord.ApplicationContext, error: discord.DiscordException):
        if not ctx.command or ctx.command.cog != self:
            return

        # 仅处理本 Cog 内部的权限检查错误
        if isinstance(error, commands.CheckFailure):
            logger.warning(
                f"用户 {ctx.author.id} ({ctx.author.name}) "
                f"因不具备管理员权限而无法执行命令 '{ctx.command.qualified_name}'。"
            )
            message = "🚫 您没有权限使用此命令。"
            try:
                if ctx.interaction.response.is_done():
                    await ctx.followup.send(message, ephemeral=True)
                else:
                    await ctx.respond(message, ephemeral=True)
            except discord.errors.NotFound:
                pass
            except Exception as e:
                logger.error(f"在处理命令 '{ctx.command.qualified_name}' 的权限错误时发生意外:", exc_info=e)
        
        # 将其他错误交给全局处理器处理，避免重复发送消息
        # logger.error(...) 调用已移至全局处理器

def setup(bot):
    bot.add_cog(Admin(bot))
