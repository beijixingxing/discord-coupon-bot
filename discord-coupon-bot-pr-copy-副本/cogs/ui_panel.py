import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup, Option
import logging
import re

logger = logging.getLogger('cog.ui_panel')

# --- Modals --- 

class DonationModal(discord.ui.Modal):
    def __init__(self, bot: discord.Bot):
        super().__init__(title="密钥捐赠")
        self.bot = bot
        self.add_item(discord.ui.InputText(
            label="粘贴您的密钥",
            placeholder="请将您的一个或多个Gemini API密钥粘贴到此处。\n如需通过文件上传，请使用 /捐赠 命令。",
            style=discord.InputTextStyle.paragraph,
            required=True
        ))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        donation_cog = self.bot.get_cog('Donation')
        if not donation_cog or not hasattr(donation_cog, '_process_donation'):
            await interaction.followup.send("❌ 捐赠功能当前不可用，请联系管理员。", ephemeral=True)
            return
            
        # 从输入框获取文本并解析
        raw_text = self.children[0].value
        keys = donation_cog._parse_raw_text(raw_text)
        
        try:
            result_embed = await donation_cog._process_donation(interaction, keys)
            await interaction.followup.send(embed=result_embed, ephemeral=True)
        except Exception as e:
            logger.error(f"处理捐赠模态框时出错: {e}", exc_info=True)
            await interaction.followup.send("🚫 处理您的捐赠时发生了一个未知的内部错误。", ephemeral=True)


class SetCooldownModal(discord.ui.Modal):
    def __init__(self, admin_cog, project_name: str):
        super().__init__(title=f"设置 {project_name} 的冷却")
        self.admin_cog = admin_cog
        self.project_name = project_name
        self.add_item(discord.ui.InputText(
            label="冷却时间（小时）",
            placeholder="请输入一个非负整数，例如 8。",
            required=True
        ))

    async def callback(self, interaction: discord.Interaction):
        hours_str = self.children[0].value
        try:
            hours = int(hours_str)
            if hours < 0:
                raise ValueError()
            await self.admin_cog._process_set_cooldown(interaction, self.project_name, hours)
        except ValueError:
            await interaction.response.send_message("❌ 请输入一个有效的非负整数。", ephemeral=True)


class UserActionProjectSelect(discord.ui.Select):
    """在执行用户操作前，选择项目。"""
    def __init__(self, bot, admin_cog, action: str):
        self.bot = bot
        self.admin_cog = admin_cog
        self.action = action
        
        projects = self.bot.project_cache
        options = [discord.SelectOption(label=p, value=p) for p in projects[:24]] # 留一个位置给全局
        
        # 增加一个“全局”选项
        options.insert(0, discord.SelectOption(label="全局操作", value="_global", description="对所有项目生效或不针对特定项目。"))
        
        super().__init__(placeholder="请选择一个项目或“全局”进行操作", options=options)

    async def callback(self, interaction: discord.Interaction):
        project_name = self.values[0]
        
        # 弹出只包含文本输入的模态框
        modal = UserActionModal(self.admin_cog, self.action, project_name)
        # 直接弹出模态框作为对本次交互的唯一响应
        await interaction.response.send_modal(modal)

class UserActionProjectSelectView(discord.ui.View):
    def __init__(self, bot, admin_cog, action: str):
        super().__init__(timeout=180)
        self.add_item(UserActionProjectSelect(bot, admin_cog, action))

class UserActionModal(discord.ui.Modal):
    """只包含文本输入的模态框，用于封禁/解封。"""
    def __init__(self, admin_cog, action: str, project_name: str):
        title_action = '封禁' if action == 'ban_user' else '解封'
        title_target = f"「{project_name}」" if project_name != "_global" else "全局"
        super().__init__(title=f"在 {title_target} {title_action}用户")
        self.admin_cog = admin_cog
        self.action = action
        self.project_name = project_name

        self.add_item(discord.ui.InputText(
            label="用户ID 或 用户名#识别号",
            placeholder="例如: 123456789012345678 或 Roo#1234",
            required=True
        ))
        self.add_item(discord.ui.InputText(label="原因", placeholder="请输入操作原因。", required=True))
        if self.action == 'ban_user':
            self.add_item(discord.ui.InputText(label="封禁时长（小时，可选）", placeholder="留空则为永久封禁。", required=False))

    async def callback(self, interaction: discord.Interaction):
        user_input = self.children[0].value
        reason = self.children[1].value
        duration = None
        if self.action == 'ban_user' and len(self.children) > 2:
            duration_str = self.children[2].value
            duration = int(duration_str) if duration_str and duration_str.isdigit() else None

        project_value = self.project_name if self.project_name != "_global" else None
        
        await interaction.response.defer(ephemeral=True)

        user = await self.admin_cog.find_user(interaction, user_input)
        if not user:
            return

        if self.action == 'ban_user':
            await self.admin_cog._process_ban_user(interaction, user, reason, project_value, duration)
        else:
            await self.admin_cog._process_unban_user(interaction, user, reason, project_value)


# --- Views --- 

class DonationTypeView(discord.ui.View):
    def __init__(self, bot: discord.Bot):
        super().__init__(timeout=180)
        self.bot = bot

    @discord.ui.button(label="📝 粘贴文本", style=discord.ButtonStyle.primary)
    async def paste_text_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        modal = DonationModal(self.bot)
        await interaction.response.send_modal(modal)
        # 禁用原始视图
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)

    @discord.ui.button(label="📄 上传 .txt 文件", style=discord.ButtonStyle.secondary)
    async def upload_txt_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        embed = discord.Embed(
            title="如何通过 .txt 文件捐赠",
            description="请使用斜杠命令 `/捐赠` 并选择 `file` 参数来上传您的 `.txt` 文件。",
            color=discord.Color.blurple()
        )
        # embed.set_image(url="https://i.imgur.com/donatetxt.png") # 根据用户反馈移除图片
        await interaction.response.send_message(embed=embed, ephemeral=True)
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)

    @discord.ui.button(label="🗂️ 上传 .zip 文件", style=discord.ButtonStyle.secondary)
    async def upload_zip_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        embed = discord.Embed(
            title="如何通过 .zip 文件捐赠",
            description="请使用斜杠命令 `/捐赠` 并选择 `file` 参数来上传您的 `.zip` 文件。",
            color=discord.Color.blurple()
        )
        # embed.set_image(url="https://i.imgur.com/donatezip.png") # 根据用户反馈移除图片
        await interaction.response.send_message(embed=embed, ephemeral=True)
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)


class ProjectSelect(discord.ui.Select):
    def __init__(self, bot: discord.Bot, projects: list[str]):
        self.bot = bot
        options = [discord.SelectOption(label=p) for p in projects[:25]]
        if not options:
            options.append(discord.SelectOption(label="暂无项目", value="_placeholder", default=True))

        super().__init__(placeholder="请选择一个要申领的项目...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "_placeholder":
            await interaction.response.edit_message(content="当前没有可供申领的项目。", view=None)
            return
            
        project_name = self.values[0]
        user_cog = self.bot.get_cog('User')

        if user_cog and hasattr(user_cog, '_process_claim'):
            # 禁用下拉菜单并显示加载中
            self.disabled = True
            await interaction.response.edit_message(content=f"正在为您申领 **{project_name}** 项目的兑换券，请稍候...", view=self.view)
            # 调用核心逻辑
            try:
                # _process_claim 会在成功时返回 True
                success = await user_cog._process_claim(interaction, project_name)
                # 仅在申领成功时删除原始消息
                if success:
                    await interaction.delete_original_response()
            except Exception as e:
                logger.error(f"申领兑换券时出错: {e}", exc_info=True)
                # 确保即使发生意外错误，用户也能收到反馈
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ 申领时发生未知错误。", ephemeral=True)
                else:
                    await interaction.followup.send("❌ 申领时发生未知错误。", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 申领功能当前不可用，请联系管理员。", ephemeral=True)


class ClaimView(discord.ui.View):
    def __init__(self, bot: discord.Bot, projects: list[str]):
        super().__init__(timeout=180) # 临时视图，180秒后超时
        self.add_item(ProjectSelect(bot, projects))



class ProjectActionSelect(discord.ui.Select):
    def __init__(self, bot: discord.Bot, projects: list[str], action: str):
        self.bot = bot
        self.action = action
        options = [discord.SelectOption(label=p) for p in projects[:25]]
        super().__init__(placeholder="请选择一个项目...", options=options)

    async def callback(self, interaction: discord.Interaction):
        project_name = self.values[0]
        admin_cog = self.bot.get_cog('Admin')
        if not admin_cog:
            await interaction.response.send_message("❌ 管理模块当前不可用。", ephemeral=True)
            return

        # 根据操作类型决定下一步
        if self.action == "delete_project":
            view = admin_cog.DeleteConfirmationView(admin_cog, project_name)
            await interaction.response.edit_message(
                content=f"**⚠️ 警告：您确定要永久删除项目 `{project_name}` 吗？**\n"
                        f"此操作不可逆，将同时删除该项目下**所有**的兑换券和封禁记录。",
                view=view
            )
        
        elif self.action == "toggle_claim":
            for item in self.view.children:
                item.disabled = True
            await interaction.response.edit_message(content=f"请为项目 **{project_name}** 选择新的申领状态：", view=self.view)
            view = ToggleClaimView(admin_cog, project_name)
            await interaction.followup.send(view=view, ephemeral=True)

        elif self.action == "set_cooldown":
            # 对于需要弹出模态框的操作，直接响应
            modal = SetCooldownModal(admin_cog, project_name)
            await interaction.response.send_modal(modal)

class ProjectActionView(discord.ui.View):
    def __init__(self, bot: discord.Bot, projects: list[str], action: str):
        super().__init__(timeout=180)
        self.add_item(ProjectActionSelect(bot, projects, action))

class ToggleClaimView(discord.ui.View):
    def __init__(self, admin_cog, project_name: str):
        super().__init__(timeout=60)
        self.admin_cog = admin_cog
        self.project_name = project_name
        # 注意：按钮由下面的 @discord.ui.button 装饰器自动添加。

    @discord.ui.button(label="开启申领", style=discord.ButtonStyle.success)
    async def enable_claim(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await self.admin_cog._process_toggle_claim(interaction, self.project_name, True)
        except Exception as e:
            logger.error(f"开启申领时出错: {e}", exc_info=True)
            await interaction.response.send_message("❌ 开启申领时发生错误。", ephemeral=True)

    @discord.ui.button(label="关闭申领", style=discord.ButtonStyle.danger)
    async def disable_claim(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await self.admin_cog._process_toggle_claim(interaction, self.project_name, False)
        except Exception as e:
            logger.error(f"关闭申领时出错: {e}", exc_info=True)
            await interaction.response.send_message("❌ 关闭申领时发生错误。", ephemeral=True)



class CogSelect(discord.ui.Select):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        # 获取所有已加载的cog名称
        cog_names = [name for name in bot.cogs.keys()]
        options = [discord.SelectOption(label=cog) for cog in cog_names]
        super().__init__(placeholder="请选择要重载的模块...", options=options)

    async def callback(self, interaction: discord.Interaction):
        cog_name = self.values[0]
        try:
            # 重载cog
            self.bot.reload_extension(f'cogs.{cog_name.lower()}')
            await interaction.response.send_message(f"✅ 模块 {cog_name} 已成功重载。", ephemeral=True)
        except Exception as e:
            logger.error(f"重载模块 {cog_name} 时出错: {e}", exc_info=True)
            await interaction.response.send_message(f"❌ 重载模块 {cog_name} 时发生错误。", ephemeral=True)

class CogSelectView(discord.ui.View):
    def __init__(self, bot: discord.Bot):
        super().__init__(timeout=180)
        self.add_item(CogSelect(bot))


class AdminPanelView(discord.ui.View):
    def __init__(self, bot: discord.Bot):
        super().__init__(timeout=180)
        self.bot = bot
        
        # Unified Select Menu for all admin actions
        self.add_item(UnifiedAdminActionSelect(bot))

class UnifiedAdminActionSelect(discord.ui.Select):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        options = [
            discord.SelectOption(label="创建新项目", value="create_project", emoji="➕", description="创建一个新的兑换券项目。"),
            discord.SelectOption(label="删除项目", value="delete_project", emoji="🗑️", description="永久删除一个项目及其所有数据。"),
            discord.SelectOption(label="开启/关闭申领", value="toggle_claim", emoji="🔄", description="为一个项目启用或禁用申领功能。"),
            discord.SelectOption(label="设置冷却时间", value="set_cooldown", emoji="⏱️", description="为一个项目设置申领冷却时间。"),
            discord.SelectOption(label="封禁用户", value="ban_user", emoji="🚫", description="禁止一个用户申领兑换券。"),
            discord.SelectOption(label="解封用户", value="unban_user", emoji="✅", description="解除用户的封禁。"),
            discord.SelectOption(label="重载模块", value="reload_cog", emoji="⚙️", description="重载一个功能模块（Cog）。"),
            discord.SelectOption(label="立即备份", value="backup_now", emoji="💾", description="立即手动触发一次数据库备份。"),
        ]
        super().__init__(placeholder="请选择一项管理操作...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        action = self.values[0]
        admin_cog = self.bot.get_cog('Admin')
        
        # 创建一个从 action value 到中文描述的映射
        action_map = {
            "create_project": "创建新项目",
            "delete_project": "删除项目",
            "toggle_claim": "开启/关闭申领",
            "set_cooldown": "设置冷却时间",
            "ban_user": "封禁用户",
            "unban_user": "解封用户",
            "reload_cog": "重载模块",
            "backup_now": "立即备份",
        }
        action_text = action_map.get(action, action)

        if not admin_cog:
            await interaction.response.send_message("❌ 管理模块当前不可用。", ephemeral=True)
            return

        # --- Project Actions ---
        if action == "create_project":
            modal = admin_cog.CreateProjectModal(admin_cog)
            await interaction.response.send_modal(modal)
        elif action in ["delete_project", "toggle_claim", "set_cooldown", "ban_user", "unban_user"]:
            projects = self.bot.project_cache
            if not projects:
                await interaction.response.send_message("当前没有可操作的项目，无法执行此操作。", ephemeral=True)
                return
            
            if action in ["ban_user", "unban_user"]:
                 view = UserActionProjectSelectView(self.bot, admin_cog, action)
            else:
                 view = ProjectActionView(self.bot, projects, action)

            await interaction.response.send_message(f"请为 **{action_text}** 操作选择一个项目：", view=view, ephemeral=True)

        # --- System Actions ---
        elif action == "reload_cog":
            view = CogSelectView(self.bot)
            await interaction.response.send_message("请选择要重载的模块：", view=view, ephemeral=True)
        elif action == "backup_now":
            await interaction.response.defer(ephemeral=True)
            if hasattr(self.bot, 'auto_backup'):
                success = await self.bot.auto_backup()
                msg = "✅ 手动备份已成功执行。" if success else "❌ 手动备份失败。"
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.followup.send("❌ 备份功能不可用。", ephemeral=True)

class MainPanelView(discord.ui.View):
    def __init__(self, bot: discord.Bot):
        super().__init__(timeout=None)  # 持久化视图
        self.bot = bot
        # 注意：所有按钮都由下面的 @discord.ui.button 装饰器自动添加，
        # 此处不再需要手动调用 self.add_item()。

    @discord.ui.button(label="📦 查看库存", style=discord.ButtonStyle.primary, custom_id="btn_view_inventory", row=0)
    async def view_inventory_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        user_cog = self.bot.get_cog('User')
        if user_cog and hasattr(user_cog, '_show_inventory'):
            try:
                await user_cog._show_inventory(interaction)
            except Exception as e:
                logger.error(f"查看库存时出错: {e}", exc_info=True)
                await interaction.response.send_message("❌ 查看库存时发生错误。", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 查看库存功能当前不可用。", ephemeral=True)

    @discord.ui.button(label="🎟️ 申领兑换券", style=discord.ButtonStyle.success, custom_id="btn_claim_coupon", row=0)
    async def claim_coupon_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        projects = self.bot.project_cache
        if not projects:
            await interaction.response.send_message("当前没有可供申领的项目。", ephemeral=True)
            return
        view = ClaimView(self.bot, projects)
        await interaction.response.send_message("请选择要申领的项目：", view=view, ephemeral=True)

    @discord.ui.button(label="📜 我的记录", style=discord.ButtonStyle.secondary, custom_id="btn_my_records", row=0)
    async def my_records_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        user_cog = self.bot.get_cog('User')
        if user_cog and hasattr(user_cog, '_process_my_claims'):
            try:
                await user_cog._process_my_claims(interaction)
            except Exception as e:
                logger.error(f"查看记录时出错: {e}", exc_info=True)
                await interaction.response.send_message("❌ 查看记录时发生错误。", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 查看记录功能当前不可用。", ephemeral=True)

    @discord.ui.button(label="💎 捐赠密钥", style=discord.ButtonStyle.blurple, custom_id="btn_donate_keys", row=1)
    async def donate_keys_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        view = DonationTypeView(self.bot)
        await interaction.response.send_message("请选择捐赠方式：", view=view, ephemeral=True)

    @discord.ui.button(label="❓ 获取帮助", style=discord.ButtonStyle.primary, custom_id="btn_get_help", row=1)
    async def get_help_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        """
        通过调用 Help cog 来显示统一的帮助信息。
        """
        help_cog = self.bot.get_cog('Help')
        if help_cog and hasattr(help_cog, '_create_help_embed'):
            await interaction.response.defer(ephemeral=True)
            is_admin = await self.bot.is_admin(interaction.user)
            embed = help_cog._create_help_embed(is_admin)
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message("❌ 帮助功能当前不可用。", ephemeral=True)

    @discord.ui.button(label="⚙️ 管理面板", style=discord.ButtonStyle.danger, custom_id="btn_admin_panel", row=1)
    async def admin_panel_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        # 检查用户是否为管理员
        if not hasattr(self.bot, 'is_admin') or not await self.bot.is_admin(interaction.user):
            await interaction.response.send_message("❌ 您没有权限访问管理面板。", ephemeral=True)
            return

        view = AdminPanelView(self.bot)
        await interaction.response.send_message("管理员面板：", view=view, ephemeral=True)

class UIPanel(commands.Cog):
    """
    一个用于提供交互式UI面板的模块。
    """
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @commands.slash_command(name="公益站", description="获取一个私人的UI控制面板。")
    async def send_panel(self, ctx: discord.ApplicationContext):
        """
        发送一个精美的、仅用户自己可见的交互式UI面板。
        """
        await ctx.defer(ephemeral=True)

        embed = discord.Embed(
            title="✨ 公益站控制面板 ✨",
            description="欢迎使用公益站机器人！请通过下方的按钮与我进行交互。",
            color=discord.Color.from_rgb(114, 137, 218)  # Discord Blurple
        )
        
        if self.bot.user and self.bot.user.display_avatar:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            embed.set_footer(text=f"由 {self.bot.user.name} 提供服务", icon_url=self.bot.user.display_avatar.url)
        
        embed.timestamp = discord.utils.utcnow()

        view = MainPanelView(self.bot)
        
        try:
            # 直接在 followup 中发送 ephemeral 消息
            await ctx.followup.send(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            logger.error(f"发送UI面板时出错: {e}", exc_info=True)
            await ctx.followup.send(f"❌ 发送面板时发生未知错误。", ephemeral=True)


# 全局变量，用于在重载时保持对持久化视图实例的引用
persistent_view_instance = None

def setup(bot: discord.Bot):
    """当模块被加载时，Pycord 会调用此函数。"""
    global persistent_view_instance
    # 创建或获取 MainPanelView 的实例
    persistent_view_instance = MainPanelView(bot)
    # 注册持久化视图，这是确保重载后按钮仍然有效的关键
    bot.add_view(persistent_view_instance)
    # 最后加载 cog
    bot.add_cog(UIPanel(bot))

def teardown(bot: discord.Bot):
    """当模块被卸载（例如重载）时，Pycord 会调用此函数。"""
    global persistent_view_instance
    # 从 bot 中移除视图，以允许在重载时重新添加
    if persistent_view_instance:
        # Pycord 的 add_view 是幂等的，但良好的实践是在卸载时进行清理
        # 注意：Pycord 2.5+ 可能没有 remove_view，add_view 会自动处理覆盖。
        # 但为了向前兼容和代码清晰，我们保留此逻辑框架。
        # 如果 remove_view 不存在，此处的逻辑可以简化或移除。
        # 经过验证，当前版本的 Pycord 不需要显式移除。
        # bot.remove_view(persistent_view_instance) # 此行可以根据具体 Pycord 版本决定是否需要
        pass