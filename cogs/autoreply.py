import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup
import yaml
import logging
import os
import time

from utils import EmbedFactory

RESPONSES_FILE = "configs/auto_responses.yml"
logger = logging.getLogger('cog.autoreply')

class AutoReply(commands.Cog):
    """
    一个用于管理和触发关键词自动回复的模块。
    规则在 auto_responses.yml 文件中按频道分组定义。
    """
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.rules = []
        self._load_config()
        self.load_rules()

    def _load_config(self):
        """从主配置文件加载本模块的特定设置。"""
        autoreply_config = self.bot.config.get('bot_settings', {}).get('autoreply', {})
        self.delete_delay = autoreply_config.get('delete_delay_seconds', 0)
        
        raw_channels = autoreply_config.get('allowed_channel_ids', [])
        if raw_channels is None:
            raw_channels = []
        # 这是全局限制，仅对 'global' 组的规则生效
        self.global_allowed_channels = {int(c_id) for c_id in raw_channels if c_id}

        logger.debug(f"自动回复配置加载: 删除延时={self.delete_delay}s, 全局允许频道={self.global_allowed_channels or '所有'}")

    def load_rules(self):
        """
        从 YAML 文件加载按频道分组的自动回复规则。
        """
        try:
            if not os.path.exists(RESPONSES_FILE):
                logger.warning(f"自动回复文件 {RESPONSES_FILE} 不存在，未加载任何规则。")
                self.rules = []
                return

            with open(RESPONSES_FILE, 'r', encoding='utf-8') as f:
                grouped_rules = yaml.safe_load(f)
            
            if not isinstance(grouped_rules, dict):
                logger.error(f"{RESPONSES_FILE} 的顶层结构必须是一个字典 (dict)，加载失败。")
                self.rules = []
                return

            flat_rules = []
            for group_key, rules_in_group in grouped_rules.items():
                if not isinstance(rules_in_group, list):
                    logger.warning(f"在 {RESPONSES_FILE} 中，组 '{group_key}' 下的内容不是一个列表，已跳过。")
                    continue
                
                for rule in rules_in_group:
                    if not isinstance(rule, dict):
                        continue
                    
                    # 为规则动态附加其所属的频道组信息
                    rule['_channel_group'] = str(group_key)

                    # 预处理 mention_target_ids
                    if 'mention_target_ids' in rule:
                        processed_ids = set()
                        raw_ids = rule.get('mention_target_ids', [])
                        for i in raw_ids:
                            if str(i).lower() == 'bot':
                                if self.bot.user:
                                    processed_ids.add(self.bot.user.id)
                                else:
                                    logger.warning("在机器人尚未完全就绪时，无法解析 'bot' 标识符。")
                            else:
                                try:
                                    processed_ids.add(int(i))
                                except (ValueError, TypeError):
                                    logger.warning(f"规则 '{rule.get('name', 'N/A')}' 中的 mention_target_id '{i}' 格式无效，已忽略。")
                        rule['mention_target_ids'] = processed_ids
                    
                    flat_rules.append(rule)
            
            self.rules = flat_rules
            logger.info(f"成功从 {RESPONSES_FILE} 加载 {len(self.rules)} 条自动回复规则。")

        except yaml.YAMLError as e:
            logger.error(f"解析 {RESPONSES_FILE} 时发生错误: {e}")
            self.rules = []
        except Exception as e:
            logger.error(f"加载 {RESPONSES_FILE} 时发生未知错误: {e}", exc_info=True)
            self.rules = []

    def _build_embed(self, embed_data: dict) -> discord.Embed:
        color_str = embed_data.get('color', '#FFFFFF')
        try:
            color_int = int(color_str.lstrip('#'), 16)
        except ValueError:
            color_int = 0xFFFFFF
        embed = discord.Embed(title=embed_data.get('title'), description=embed_data.get('description'), color=color_int)
        if url := embed_data.get('url'):
            embed.url = url
        if thumb_url := embed_data.get('thumbnail_url'):
            embed.set_thumbnail(url=thumb_url)
        if image_url := embed_data.get('image_url'):
            embed.set_image(url=image_url)
        if author_info := embed_data.get('author'):
            embed.set_author(name=author_info.get('name', ''), url=author_info.get('url'), icon_url=author_info.get('icon_url'))
        if footer_info := embed_data.get('footer'):
            embed.set_footer(text=footer_info.get('text'), icon_url=footer_info.get('icon_url'))
        for field in embed_data.get('fields', []):
            embed.add_field(name=field.get('name'), value=field.get('value'), inline=field.get('inline', False))
        return embed

    def _add_delete_countdown_to_embed(self, embed: discord.Embed, delete_seconds: int):
        if not isinstance(embed, discord.Embed) or delete_seconds <= 0:
            return
        delete_timestamp = int(time.time()) + delete_seconds
        countdown_text = f"*本消息将在 <t:{delete_timestamp}:R> 自动删除。*"
        embed.add_field(name="\u200b", value=countdown_text, inline=False)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not self.rules:
            return

        raw_content = message.content
        channel_id = message.channel.id
        parent_id = getattr(message.channel, 'parent_id', None)

        for rule in self.rules:
            rule_name = rule.get('name', 'N/A')
            channel_group = rule.get('_channel_group')

            # --- 频道检查逻辑 ---
            if channel_group == 'global':
                # 如果是全局规则，检查是否受全局频道列表限制
                if self.global_allowed_channels:
                    if channel_id not in self.global_allowed_channels and parent_id not in self.global_allowed_channels:
                        continue # 不在全局允许列表中，跳过
            else:
                # 如果是特定频道规则，检查ID是否匹配
                try:
                    target_channel_id = int(channel_group)
                    if channel_id != target_channel_id and parent_id != target_channel_id:
                        continue # 不在规则指定的频道中，跳过
                except (ValueError, TypeError):
                    logger.warning(f"规则 '{rule_name}' 的频道组 '{channel_group}' 不是有效的ID，已跳过。")
                    continue
            
            # --- 触发器检查逻辑 ---
            is_triggered = False
            trigger_type = ""

            # 新的统一提及检查逻辑
            target_ids = rule.get('mention_target_ids', set())
            # 只有在不是回复的情况下，才检查提及
            if target_ids and message.mentions and message.reference is None:
                mentioned_user = next((user for user in message.mentions if user.id in target_ids), None)
                if mentioned_user:
                    is_triggered = True
                    # 区分是提及机器人还是其他用户
                    if mentioned_user.id == self.bot.user.id:
                        trigger_type = "提及机器人"
                    else:
                        trigger_type = f"提及用户: {mentioned_user.mention}"

            if not is_triggered:
                keywords_to_check = rule.get('keywords', [])
                if keywords_to_check:
                    matched_keyword = next((k for k in keywords_to_check if k.lower() in raw_content.lower()), None)
                    if matched_keyword:
                        is_triggered = True
                        trigger_type = f"关键词: '{matched_keyword}'"
            
            if not is_triggered:
                continue

            # --- 执行动作 ---
            logger.info(f"消息匹配成功！规则: '{rule_name}', 触发方式: {trigger_type}, 频道组: {channel_group}")
            action = rule.get('action')
            delete_after = self.delete_delay if self.delete_delay > 0 else None
            if action == 'trigger_help_command':
                help_cog = self.bot.get_cog('Help')
                if help_cog and hasattr(help_cog, '_create_help_embed'):
                    is_admin = await self.bot.is_admin(message.author)
                    embed = help_cog._create_help_embed(is_admin)
                    if embed and delete_after:
                        self._add_delete_countdown_to_embed(embed, delete_after)
                    await message.reply(embed=embed, mention_author=False, delete_after=delete_after)
            elif reply_data := rule.get('reply'):
                content = reply_data.get('content')
                embed = None
                if embed_data := reply_data.get('embed'):
                    embed = self._build_embed(embed_data)
                elif image_url := reply_data.get('image_url'):
                    embed = discord.Embed().set_image(url=image_url)
                if embed and delete_after:
                    self._add_delete_countdown_to_embed(embed, delete_after)
                try:
                    await message.reply(content=content, embed=embed, mention_author=False, delete_after=delete_after)
                except discord.errors.HTTPException as e:
                    logger.error(f"发送自动回复失败 (规则: '{rule_name}'): {e}")
            break

    autoreply_group = SlashCommandGroup("自动回复", "管理自动回复模块")

    async def cog_check(self, ctx: discord.ApplicationContext) -> bool:
        """此检查应用于本模块中的所有命令，确保只有管理员可以执行。"""
        return await self.bot.is_admin(ctx.author)

    async def on_application_command_error(self, ctx: discord.ApplicationContext, error: discord.DiscordException):
        """处理本模块命令的特定错误。"""
        if isinstance(error, commands.CheckFailure):
            await ctx.respond("🚫 只有机器人管理员才能执行此操作。", ephemeral=True)
        else:
            # 对于其他错误，可以记录或让全局处理器处理
            logger.error(f"在 autoreply cog 中发生错误: {error}", exc_info=True)

    @autoreply_group.command(name="状态", description="检查自动回复模块的状态。")
    async def status(self, ctx: discord.ApplicationContext):
        """提供关于自动回复模块当前状态的诊断信息。"""
        embed = discord.Embed(title="⚙️ 自动回复模块状态", color=discord.Color.blue())
        embed.add_field(name="已加载规则数量", value=f"`{len(self.rules)}` 条", inline=False)
        embed.set_footer(text=f"使用 /自动回复 重载 命令来应用来自文件的最新更改。")
        
        await ctx.respond(embed=embed, ephemeral=True)

    @autoreply_group.command(name="重载", description="从 YAML 文件重新加载自动回复规则。")
    async def reload(self, ctx: discord.ApplicationContext):
        """重新加载所有自动回复规则和相关配置。"""
        await ctx.defer(ephemeral=True)
        
        try:
            self._load_config() # 重新加载主配置中本模块的设置
            self.load_rules()   # 重新加载 auto_responses.yml
            await ctx.followup.send(f"✅ 成功！已重新加载 **{len(self.rules)}** 条自动回复规则。", ephemeral=True)
        except Exception as e:
            logger.error(f"管理员 {ctx.author.name} 尝试重载自动回复规则时失败: {e}", exc_info=True)
            await ctx.followup.send(f"❌ 重载失败。请检查日志获取详细信息。", ephemeral=True)


def setup(bot):
    bot.add_cog(AutoReply(bot))