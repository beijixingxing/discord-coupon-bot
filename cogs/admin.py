import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup, Option
from utils import project_autocompleter, is_admin
from typing import Optional
import logging
import io
import zipfile
from datetime import datetime, timezone

logger = logging.getLogger('cog.admin')

# --- Cog Class ---
class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    admin = SlashCommandGroup(
        "管理",
        "兑换券机器人管理命令",
        checks=[is_admin()]
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
    @admin.command(name="添加兑换券", description="批量添加兑换券（支持.txt或.zip文件）。")
    async def add_coupons(self, ctx,
                          project: Option(str, "要添加兑换券的项目。", autocomplete=project_autocompleter),
                          file: Option(discord.Attachment, "包含兑换券的.txt文件或包含多个.txt的.zip文件。"),
                          expiry_days: Option(int, "兑换券有效期天数（留空则为永久）。", min_value=1, required=False)):
      
        await ctx.defer(ephemeral=True)

        final_message = "❌ 处理时发生未知错误。" # 默认的失败消息

        try:
            # --- 逻辑块：只负责计算结果，不与Discord交互 ---
            filename = file.filename.lower()
            all_codes = []
            processed_files_count = 0

            if filename.endswith('.zip'):
                try:
                    zip_content = await file.read()
                    with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
                        for file_info in zf.infolist():
                            if file_info.filename.lower().endswith('.txt') and not file_info.is_dir():
                                processed_files_count += 1
                                with zf.open(file_info) as txt_file:
                                    content = txt_file.read().decode('utf-8')
                                    codes = [code.strip() for code in content.splitlines() if code.strip()]
                                    all_codes.extend(codes)
                except zipfile.BadZipFile:
                    final_message = "❌ 上传的 .zip 文件已损坏或格式不正确。"
                    # 立即返回，不执行后续逻辑
                    return
          
            elif filename.endswith('.txt'):
                processed_files_count = 1
                file_content = await file.read()
                codes = [code.strip() for code in file_content.decode('utf-8').splitlines() if code.strip()]
                all_codes.extend(codes)
          
            else:
                final_message = "❌ 请上传一个有效的 `.txt` 或 `.zip` 文件。"
                # 立即返回，不执行后续逻辑
                return

            # 检查是否有有效的兑换码
            if not all_codes:
                final_message = "🤷 文件中未找到任何有效的兑换券代码。"
                return

            # 数据库操作
            result = await self.bot.db_manager.add_coupons(project, all_codes, expiry_days)
            if result is None:
                final_message = f"❌ 未找到项目 '{project}'。"
            else:
                newly_added, duplicates, _ = result
                file_type_msg = f"{processed_files_count} 个 .txt 文件" if filename.endswith('.zip') else ".txt 文件"
              
                final_message = (
                    f"✅ **为项目 '{project}' 处理完毕！**\n"
                    f"- **来源**: 已处理 {file_type_msg}\n"
                    f"- **新增兑换券**: **{newly_added}**\n"
                    f"- **忽略的重复券**: **{duplicates}**\n"
                    f"- **总计**: **{len(all_codes)}**"
                )

        except Exception as e:
            logger.error(f"处理添加兑换券命令时发生意外错误: {str(e)}", exc_info=True)
            final_message = f"🚫 处理文件时发生了一个内部错误，请检查日志。错误: {str(e)}"
      
        finally:
            # --- 响应块：唯一出口，负责与Discord交互 ---
            try:
                await ctx.interaction.edit_original_response(content=final_message)
            except discord.errors.NotFound:
                logger.warning("尝试编辑响应消息失败，可能已被用户关闭。")
            except Exception as e:
                logger.error(f"编辑最终响应消息时发生未知错误: {e}", exc_info=True)

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
            embed = discord.Embed(
                title="🚫 用户封禁公告",
                description=f"用户 **{user.mention}** (`{user.id}`) 已被管理员 **{ctx.author.mention}** 封禁。",
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="详情", value=message, inline=False)
            await ctx.respond(embed=embed) # 公开消息
        else:
            await ctx.respond(f"❌ 封禁失败。{message}", ephemeral=True) # 失败消息仍然是临时的

    @admin.command(name="解封", description="解除用户的封禁。")
    async def unban(self, ctx,
                    user: Option(discord.Member, "要解封的用户。"),
                    reason: Option(str, "解封的原因。"),
                    project: Option(str, "要解封的项目（留空则为全局）。", autocomplete=project_autocompleter, required=False)):
        
        success, message = await self.bot.db_manager.unban_user(user.id, project)
        if success:
            embed = discord.Embed(
                title="✅ 用户解封公告",
                description=f"用户 **{user.mention}** (`{user.id}`) 的封禁已被管理员 **{ctx.author.mention}** 解除。",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="范围", value=message, inline=False)
            embed.add_field(name="理由", value=reason, inline=False)
            await ctx.respond(embed=embed) # 公开消息
        else:
            await ctx.respond(f"❌ 解封失败。{message}", ephemeral=True) # 失败消息仍然是临时的

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