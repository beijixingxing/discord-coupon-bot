import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup, Option
from utils import project_autocompleter, EmbedFactory
from typing import Optional
import logging
import io
import zipfile
from datetime import datetime, timezone
import re
from constants import ERROR_COLOR, SUCCESS_COLOR

logger = logging.getLogger('cog.admin')

async def cog_autocompleter(ctx: discord.AutocompleteContext) -> list[str]:
    """为已加载的 cogs 提供自动补全。"""
    # self.bot.extensions 的键是 'cogs.admin', 'cogs.autoreply' 等
    # 我们只需要 'admin', 'autoreply' 这部分
    loaded_cogs = [
        name.split('.')[-1] for name in ctx.bot.extensions
        if name.startswith('cogs.')
    ]
    return [
        cog for cog in loaded_cogs
        if cog.lower().startswith(ctx.value.lower())
    ]

# --- Cog Class ---
class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # 将 is_admin() 检查应用于此 Cog 中的所有命令
    def cog_check(self, ctx: discord.ApplicationContext) -> bool:
        # 从 bot 实例中获取在启动时加载的管理员ID列表
        admin_ids = getattr(ctx.bot, 'admin_user_ids', set())
        return ctx.author.id in admin_ids

    admin = SlashCommandGroup(
        "管理",
        "兑换券机器人管理命令"
    )

    async def find_user(self, interaction: discord.Interaction, user_input: str) -> Optional[discord.User]:
        """
        根据用户ID或'用户名#识别号'查找用户。
        优化逻辑：优先通过ID进行全局查找，然后才在服务器内部按名称查找。
        """
        user: Optional[discord.User] = None

        # 1. 优先尝试通过ID进行全局查找 (bot.fetch_user)
        if re.match(r'^\d{17,20}$', user_input):
            try:
                user = await self.bot.fetch_user(int(user_input))
            except discord.NotFound:
                # 如果全局都找不到这个ID，那它就是无效的
                await interaction.followup.send(f"❌ 未能通过ID `{user_input}` 找到任何 Discord 用户。", ephemeral=True)
                return None
            except discord.HTTPException as e:
                logger.error(f"通过全局ID获取用户时发生HTTP错误: {e}")
                await interaction.followup.send("🚫 查询用户时发生网络错误，请稍后再试。", ephemeral=True)
                return None
        
        # 2. 如果输入不是ID，或者ID查找失败，则尝试在当前服务器内按名称查找
        else:
            if not interaction.guild:
                await interaction.followup.send("❌ 按用户名查找只能在服务器内进行。", ephemeral=True)
                return None
            
            # 尝试按 "用户名#识别号" 格式查找
            if '#' in user_input:
                try:
                    name, discriminator = user_input.rsplit('#', 1)
                    user = discord.utils.get(interaction.guild.members, name=name, discriminator=discriminator)
                except ValueError:
                    pass # 格式不正确，忽略
            
            # 如果上面没找到，就尝试只按用户名/昵称模糊查找
            if user is None:
                # `interaction.guild.get_member_named` 是一个更方便的查找工具
                user = interaction.guild.get_member_named(user_input)

        if user is None:
            await interaction.followup.send(f"❌ 未能在本服务器中找到用户 `{user_input}`。请检查输入是否正确。", ephemeral=True)
            return None
        
        return user

    # --- Project Management Modals & Views ---
    class CreateProjectModal(discord.ui.Modal):
        def __init__(self, admin_cog):
            super().__init__(title="创建新项目")
            self.admin_cog = admin_cog
            self.add_item(discord.ui.InputText(
                label="项目名称",
                placeholder="请输入新项目的唯一名称。",
                required=True
            ))

        async def callback(self, interaction: discord.Interaction):
            project_name = self.children[0].value
            success, message = await self.admin_cog.bot.db_manager.create_project(project_name)
            if success:
                await interaction.response.send_message(f"✅ {message}", ephemeral=True)
                await self.admin_cog.bot.update_project_cache()
            else:
                await interaction.response.send_message(f"❌ {message}", ephemeral=True)

    # --- Project Management Commands ---
    class DeleteConfirmationView(discord.ui.View):
        """一个用于确认删除操作的视图。"""
        def __init__(self, admin_cog, project_name: str):
            super().__init__(timeout=30.0)
            self.admin_cog = admin_cog
            self.project_name = project_name

        async def _disable_and_stop(self, interaction: discord.Interaction, content: str):
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(content=content, view=self)
            self.stop()

        @discord.ui.button(label="确认删除", style=discord.ButtonStyle.danger)
        async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
            await self._disable_and_stop(interaction, f"正在删除项目 **{self.project_name}**...")
            success, message = await self.admin_cog.bot.db_manager.delete_project(self.project_name)
            if success:
                await self.admin_cog.bot.update_project_cache()
                self.admin_cog.bot.invalidate_stock_cache(self.project_name)
                await interaction.followup.send(f"✅ {message}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ {message}", ephemeral=True)

        @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
        async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction):
            await self._disable_and_stop(interaction, "操作已取消。")

    # --- Coupon Management Commands ---
    async def _parse_txt_file(self, file: discord.Attachment) -> list[str]:
        """解析 .txt 文件并返回兑换码列表。"""
        file_content_bytes = await file.read()
        try:
            content = file_content_bytes.decode('utf-8')
        except UnicodeDecodeError:
            try:
                content = file_content_bytes.decode('gbk')
                logger.warning(f"文件 '{file.filename}' 使用 GBK 编码进行解码。")
            except UnicodeDecodeError:
                raise ValueError(f"文件 '{file.filename}' 的编码格式不受支持，请使用 UTF-8 或 GBK 编码。")
        return [code.strip() for code in content.splitlines() if code.strip()]

    async def _parse_zip_file(self, file: discord.Attachment) -> tuple[list[str], int]:
        """解析 .zip 文件并返回兑换码列表和处理的文件数。"""
        all_codes = []
        processed_files_count = 0
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
            raise ValueError("上传的 .zip 文件已损坏或格式不正确。")
        return all_codes, processed_files_count

    async def _process_add_coupons(self, interaction: discord.Interaction, project: str, file: discord.Attachment, expiry_days: Optional[int]):
        """处理添加兑换券的核心逻辑。"""
        try:
            filename = file.filename.lower()
            all_codes = []
            processed_files_count = 0
            file_type_msg = ""

            if filename.endswith('.zip'):
                all_codes, processed_files_count = await self._parse_zip_file(file)
                file_type_msg = f"{processed_files_count} 个 .txt 文件"
            elif filename.endswith('.txt'):
                all_codes = await self._parse_txt_file(file)
                processed_files_count = 1
                file_type_msg = ".txt 文件"
            else:
                await interaction.followup.send("❌ 请上传一个有效的 `.txt` 或 `.zip` 文件。", ephemeral=True)
                return

            if not all_codes:
                await interaction.followup.send("🤷 文件中未找到任何有效的兑换券代码。", ephemeral=True)
                return

            result = await self.bot.db_manager.add_coupons(project, all_codes, expiry_days)
            if result is None:
                await interaction.followup.send(f"❌ 未找到项目 '{project}'。", ephemeral=True)
                return

            self.bot.invalidate_stock_cache(project)
            newly_added, duplicates, _ = result
            
            final_message = (
                f"✅ **为项目 '{project}' 处理完毕！**\n"
                f"- **来源**: 已处理 {file_type_msg}\n"
                f"- **新增兑换券**: **{newly_added}**\n"
                f"- **忽略的重复券**: **{duplicates}**\n"
                f"- **总计**: **{len(all_codes)}**"
            )
            await interaction.followup.send(final_message, ephemeral=True)

        except ValueError as e:
            await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)
        except Exception as e:
            logger.error(f"处理添加兑换券命令时发生意外错误: {str(e)}", exc_info=True)
            await interaction.followup.send(f"🚫 处理文件时发生了一个内部错误，请检查日志。", ephemeral=True)

    @admin.command(name="添加兑换券", description="批量添加兑换券（支持.txt或.zip文件）。")
    async def add_coupons(self, ctx: discord.ApplicationContext,
                          project: Option(str, "要添加兑换券的项目。", autocomplete=project_autocompleter),
                          file: Option(discord.Attachment, "包含兑换券的.txt文件或包含多个.txt的.zip文件。"),
                          expiry_days: Option(int, "兑换券有效期天数（留空则为永久）。", min_value=1, required=False)):
        await ctx.defer(ephemeral=True)
        await self._process_add_coupons(ctx.interaction, project, file, expiry_days)

    # --- Settings Commands ---
    async def _process_toggle_claim(self, interaction: discord.Interaction, project_name: str, new_status: bool):
        """处理开关申领的核心逻辑。"""
        success = await self.bot.db_manager.set_project_setting(project_name, 'is_claim_active', new_status)
        status_text = "开启" if new_status else "关闭"
        if success:
            await interaction.response.send_message(f"✅ 项目 **{project_name}** 的申领功能已 **{status_text}**。", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ 未找到项目 '{project_name}'。", ephemeral=True)

    async def _process_set_cooldown(self, interaction: discord.Interaction, project_name: str, hours: int):
        """处理设置冷却时间的核心逻辑。"""
        success = await self.bot.db_manager.set_project_setting(project_name, 'claim_cooldown_hours', hours)
        if success:
            await interaction.response.send_message(f"✅ 项目 **{project_name}** 的冷却时间已设置为 **{hours}** 小时。", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ 未找到项目 '{project_name}'。", ephemeral=True)

    # --- User Moderation Commands ---
    async def _process_ban_user(self, interaction: discord.Interaction, user: discord.User, reason: str, project: Optional[str], duration_hours: Optional[int]):
        """处理用户封禁的核心逻辑。"""
        # 确保我们有一个有效的 guild 上下文来发送公告
        if not interaction.guild:
            await interaction.response.send_message("❌ 此操作只能在服务器内执行。", ephemeral=True)
            return

        success, message = await self.bot.db_manager.ban_user(user.id, project, reason, duration_hours)
        
        # 无论成功与否，首先响应发起操作的管理员
        if success:
            response_message = f"✅ 已成功封禁用户 **{user.display_name}**。"
            # 如果交互尚未响应，则使用 response.send_message
            if not interaction.response.is_done():
                await interaction.response.send_message(response_message, ephemeral=True)
            else: # 否则使用 followup
                await interaction.followup.send(response_message, ephemeral=True)
        else:
            response_message = f"❌ 封禁失败: {message}"
            if not interaction.response.is_done():
                await interaction.response.send_message(response_message, ephemeral=True)
            else:
                await interaction.followup.send(response_message, ephemeral=True)
            return # 失败则不发送公告

        # 成功后，构建并发送公开的封禁公告
        description = f"用户 **{user.mention}** (`{user.id}`) 已被管理员 **{interaction.user.mention}** 封禁。"
        embed = EmbedFactory.error("用户封禁公告", description)
        embed.add_field(name="详情", value=message, inline=False)
        
        # 尝试在原始交互频道发送公告
        try:
            await interaction.channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send("⚠️ 无法在此频道发送公开的封禁公告（权限不足）。", ephemeral=True)
        except Exception as e:
            logger.error(f"发送封禁公告时出错: {e}", exc_info=True)
            await interaction.followup.send("⚠️ 发送公开封禁公告时发生错误。", ephemeral=True)

    async def _process_unban_user(self, interaction: discord.Interaction, user: discord.User, reason: str, project: Optional[str]):
        """处理用户解封的核心逻辑。"""
        if not interaction.guild:
            await interaction.response.send_message("❌ 此操作只能在服务器内执行。", ephemeral=True)
            return

        success, message = await self.bot.db_manager.unban_user(user.id, project)

        if success:
            response_message = f"✅ 已成功解封用户 **{user.display_name}**。"
            if not interaction.response.is_done():
                await interaction.response.send_message(response_message, ephemeral=True)
            else:
                await interaction.followup.send(response_message, ephemeral=True)
        else:
            response_message = f"❌ 解封失败: {message}"
            if not interaction.response.is_done():
                await interaction.response.send_message(response_message, ephemeral=True)
            else:
                await interaction.followup.send(response_message, ephemeral=True)
            return

        # 成功后发送公开解封公告
        description = f"用户 **{user.mention}** (`{user.id}`) 的封禁已被管理员 **{interaction.user.mention}** 解除。"
        embed = EmbedFactory.success("用户解封公告", description)
        embed.add_field(name="范围", value=message, inline=False)
        embed.add_field(name="理由", value=reason, inline=False)
        
        try:
            await interaction.channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send("⚠️ 无法在此频道发送公开的解封公告（权限不足）。", ephemeral=True)
        except Exception as e:
            logger.error(f"发送解封公告时出错: {e}", exc_info=True)
            await interaction.followup.send("⚠️ 发送公开解封公告时发生错误。", ephemeral=True)

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