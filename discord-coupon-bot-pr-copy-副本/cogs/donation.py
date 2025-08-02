import discord
from discord.ext import commands
from discord.commands import Option
import logging
import re
import os
import io
import zipfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import List, Optional, Set

from utils import EmbedFactory
from constants import GEMINI_API_KEY_REGEX
from database import DatabaseManager # <<< 导入

logger = logging.getLogger('cog.donation')
DONATION_DIR = "/app/data/donations"

class Donation(commands.Cog):
    """处理用户密钥捐赠的功能模块。"""

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.db_manager: DatabaseManager = bot.db_manager # <<< 获取数据库管理器实例
        # 编译正则表达式以提高性能
        # 修改正则表达式，移除 ^ 和 $，以便在文本中查找而不是匹配整行
        self.key_regex = re.compile(GEMINI_API_KEY_REGEX.strip('^$'))
        # 确保捐赠目录存在
        os.makedirs(DONATION_DIR, exist_ok=True)

    def _extract_keys_from_text(self, text: str) -> List[str]:
        """
        从大段文本中直接查找所有符合 Gemini API Key 格式的字符串。
        """
        return self.key_regex.findall(text)

    def _parse_raw_text(self, text: str) -> List[str]:
        """从直接粘贴的文本中分离出所有可能的密钥字符串。"""
        return self._extract_keys_from_text(text)

    async def _parse_txt_file(self, file: discord.Attachment) -> List[str]:
        """解析 .txt 文件并提取所有可能的密钥。"""
        file_content_bytes = await file.read()
        try:
            content = file_content_bytes.decode('utf-8')
        except UnicodeDecodeError:
            try:
                content = file_content_bytes.decode('gbk')
            except UnicodeDecodeError:
                raise ValueError(f"文件 '{file.filename}' 的编码格式不受支持，请使用 UTF-8 或 GBK。")
        return self._extract_keys_from_text(content)

    async def _parse_zip_file(self, file: discord.Attachment) -> List[str]:
        """解析 .zip 文件，提取其中所有 .txt 文件的内容。"""
        all_keys = []
        try:
            zip_content = await file.read()
            with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
                for file_info in zf.infolist():
                    if file_info.filename.lower().endswith('.txt') and not file_info.is_dir():
                        with zf.open(file_info) as txt_file:
                            try:
                                content = txt_file.read().decode('utf-8')
                                all_keys.extend(self._extract_keys_from_text(content))
                            except Exception:
                                logger.warning(f"无法解析zip压缩包中的文件: {file_info.filename}")
                                continue
        except zipfile.BadZipFile:
            raise ValueError("上传的 .zip 文件已损坏或格式不正确。")
        return all_keys

    def _filter_and_clean_keys(self, keys: List[str]) -> Set[str]:
        """对找到的密钥列表进行去重。"""
        # 由于提取时已经使用了正则表达式，这里只需要去重即可。
        return set(keys)

    async def _process_donation(
        self,
        interaction: discord.Interaction,
        all_potential_keys: List[str]
    ) -> discord.Embed:
        """
        处理捐赠的核心逻辑，返回一个用于反馈的 Embed。
        """
        # --- 1. 检查捐赠冷却 ---
        can_donate, remaining_cooldown = await self.db_manager.check_and_update_donation_cooldown(interaction.user.id)
        if not can_donate and remaining_cooldown:
            # 从配置中动态获取冷却时间
            cooldown_hours = self.bot.config.get('bot_settings', {}).get('donation_cooldown_hours', 1)
            minutes, seconds = divmod(int(remaining_cooldown.total_seconds()), 60)
            embed = discord.Embed(
                title="操作过于频繁",
                description=f"您在最近 {cooldown_hours} 小时内已经捐赠过了，请在 **{minutes}分钟 {seconds}秒** 后再试。感谢您的慷慨！",
                color=discord.Color.orange()
            )
            return embed

        # --- 2. 提取和验证密钥 ---
        if not all_potential_keys:
            return EmbedFactory.warning("操作提醒", "未能从您的输入中找到任何内容。")

        valid_keys = self._filter_and_clean_keys(all_potential_keys)
      
        if not valid_keys:
            return EmbedFactory.info("感谢您的心意", "但在您提供的内容中未能找到有效格式的Gemini API密钥。")

        # --- 3. 与数据库对比，分离新旧密钥 ---
        existing_keys_in_db = await self.db_manager.get_all_gemini_key_values()
        new_keys_to_add = valid_keys - existing_keys_in_db
        duplicate_keys = valid_keys.intersection(existing_keys_in_db)
        quantity_new = len(new_keys_to_add)
        quantity_duplicate = len(duplicate_keys)

        # --- 4. 将新密钥存入数据库 ---
        if quantity_new > 0:
            await self.db_manager.add_gemini_keys(
                keys=list(new_keys_to_add),
                donator_user_id=interaction.user.id
            )

        total_found = len(all_potential_keys)
        total_valid = len(valid_keys)
        total_ignored = total_found - total_valid

        # --- 5. 创建本次捐赠的单独日志文件 ---
        utc_now = datetime.now(timezone.utc)
        beijing_tz = ZoneInfo('Asia/Shanghai')
        beijing_now = utc_now.astimezone(beijing_tz)
      
        # --- [修正] 更可靠地获取用户昵称和用户名 ---
        # 优先使用服务器内的 Member 对象，如果不在服务器内则回退到 User 对象
        if interaction.guild:
            # 尝试从服务器成员列表中获取，以确保数据最新
            member = interaction.guild.get_member(interaction.user.id) or interaction.user
        else:
            member = interaction.user
      
        user_display_name = member.display_name
        user_name = member.name
      
        # --- [修正] 创建新的、符合要求的文件名 ---
        # 1. 清理昵称，使其适用于文件名
        safe_display_name = re.sub(r'[\\/*?:"<>|]', "", user_display_name).replace(" ", "_")[:50]
        # 2. 使用新的时间格式
        timestamp_str = beijing_now.strftime('%Y-%m-%d_%H-%M')
        # 3. 组合成新文件名
        filename = f"{timestamp_str}_{safe_display_name}.txt"
        filepath = os.path.join(DONATION_DIR, filename)
      
        # --- [修正] 使用正确获取到的昵称和用户名写入文件头 ---
        logger.info(f"正在创建本次捐赠的日志文件: {filepath}")
        with open(filepath, 'w', encoding='utf-8') as f:
            header = (
                f"# 日期时间: {beijing_now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
                f"# 捐赠者信息\n"
                f"# 昵称 (Display Name): {user_display_name}\n"
                f"# 用户名 (Username): {user_name}\n"
                f"# 用户ID (User ID): {interaction.user.id}\n"
                f"# 捐赠数量 (新增/重复): {quantity_new} / {quantity_duplicate}\n"
                f"# ---------------------------------------\n"
            )
            f.write(header)
            for key in sorted(list(valid_keys)):
                f.write(f"{key}\n")
        logger.info("捐赠日志文件创建成功。")

        # --- 6. [修正] 使用正确的用户昵称和用户名记录到数据库 ---
        await self.db_manager.log_donation(
            user_id=interaction.user.id,
            user_name=user_name,
            user_display_name=user_display_name,
            quantity_new=quantity_new,
            quantity_duplicate=quantity_duplicate,
            file_path=filepath
        )

        # --- 7. [已移除] 不再将Key写入总汇文件，数据库是唯一的数据源 ---

        # --- 8. 构建用户反馈 ---
        if quantity_new == 0 and quantity_duplicate > 0:
            description = (
                "我们检查后发现，您提供的所有有效密钥都已经是我们库存的一部分了。\n\n"
                f"• **已存在的密钥**: **{quantity_duplicate}** 个\n"
                f"• **格式无效内容**: **{total_ignored}** 个\n\n"
                "我们依然非常感谢您的慷慨与支持！💖"
            )
        else:
            description = (
                f"我们已收到并处理了您的捐赠，衷心感谢您的支持！\n\n"
                f"• **成功入库新增密钥**: **{quantity_new}** 个\n"
                f"• **发现已存在的密钥**: **{quantity_duplicate}** 个\n"
                f"• **忽略格式无效内容**: **{total_ignored}** 个"
            )
      
        self.bot.loop.create_task(self._send_public_announcement(interaction, quantity_new, quantity_duplicate))
      
        return EmbedFactory.success("捐赠成功！", description)

    @commands.slash_command(
        name="donate",
        description="Donate API keys via text or file.",
        name_localizations={'zh-CN': '捐赠'},
        description_localizations={'zh-CN': '通过文本或文件捐赠API密钥。'}
    )
    async def unified_donate(
        self,
        ctx: discord.ApplicationContext,
        text: Option(
            str,
            "Paste the text containing one or more keys.",
            name_localizations={'zh-CN': '文本'},
            description_localizations={'zh-CN': '粘贴包含一个或多个密钥的文本。'},
            required=False
        ),
        file: Option(
            discord.Attachment,
            "Upload a .txt or .zip file containing the keys.",
            name_localizations={'zh-CN': '文件'},
            description_localizations={'zh-CN': '上传包含密钥的.txt或.zip文件。'},
            required=False
        )
    ):
        """统一处理来自文本或文件的密钥捐赠。"""
        await ctx.defer(ephemeral=True)

        if not text and not file:
            await ctx.followup.send("🤔 请至少提供 `text` 或 `file` 中的一个参数。", ephemeral=True)
            return

        all_potential_keys = []
        try:
            # 优先处理文件
            if file:
                filename = file.filename.lower()
                if filename.endswith('.txt'):
                    all_potential_keys.extend(await self._parse_txt_file(file))
                elif filename.endswith('.zip'):
                    all_potential_keys.extend(await self._parse_zip_file(file))
                else:
                    await ctx.followup.send("❌ 不支持的文件类型。请上传 `.txt` 或 `.zip` 文件。", ephemeral=True)
                    return
            # 如果没有文件，再处理文本
            elif text:
                all_potential_keys = self._parse_raw_text(text)

            result_embed = await self._process_donation(ctx.interaction, all_potential_keys)
            await ctx.followup.send(embed=result_embed, ephemeral=True)

        except ValueError as e:
            await ctx.followup.send(f"❌ 处理失败: {str(e)}", ephemeral=True)
        except Exception as e:
            logger.error(f"处理捐赠命令时发生意外错误: {e}", exc_info=True)
            await ctx.followup.send("🚫 处理您的捐赠时发生了一个未知的内部错误，管理员已收到通知。", ephemeral=True)

    async def process_text_donation(self, interaction: discord.Interaction, raw_text: str):
        """
        公开的接口，用于处理来自UI（如Modal）的纯文本捐赠。
        """
        # 注意：这个方法现在负责 defer 和 followup
        # await interaction.response.defer(ephemeral=True, thinking=True) # defer 由调用方处理
        try:
            keys = self._parse_raw_text(raw_text)
            result_embed = await self._process_donation(interaction, keys)
            await interaction.followup.send(embed=result_embed, ephemeral=True)
        except Exception as e:
            logger.error(f"处理文本捐赠时出错: {e}", exc_info=True)
            await interaction.followup.send("🚫 处理您的捐赠时发生了一个未知的内部错误。", ephemeral=True)

    async def _send_public_announcement(self, interaction: discord.Interaction, new_count: int, duplicate_count: int):
        """如果配置开启，则发送公开的感谢公告。"""
        config = self.bot.config.get('bot_settings', {}).get('donation_announcement', {})
        
        if not config.get('enabled') or new_count == 0:
            return

        channel_ids = config.get('channel_ids', [])
        embed_data = config.get('embed')

        if not channel_ids or not embed_data or not isinstance(embed_data, dict):
            logger.warning("捐赠公告已启用，但 channel_ids 或 embed 格式不正确，已跳过。")
            return

        # --- 构建 Embed ---
        def format_string(text: Optional[str]) -> str:
            if not text: return ""
            
            # 智能格式化：如果用户在模板中使用了 {user_mention}，
            # 则自动将其他可能重复的用户信息置空，以避免冗余。
            if '{user_mention}' in text:
                display_name = ""
                user_name = ""
            else:
                display_name = interaction.user.display_name
                user_name = interaction.user.name

            return text.format(
                user_mention=interaction.user.mention,
                user_name=user_name,
                user_display_name=display_name,
                new_count=new_count,
                duplicate_count=duplicate_count
            )

        try:
            color_str = embed_data.get('color', '#FFFFFF')
            color_int = int(color_str.lstrip('#'), 16)
        except (ValueError, TypeError):
            color_int = 0xFFFFFF

        embed = discord.Embed(
            title=format_string(embed_data.get('title')),
            description=format_string(embed_data.get('description')),
            color=color_int
        )
        if thumb_url := embed_data.get('thumbnail_url'):
            embed.set_thumbnail(url=thumb_url)
        
        if footer_data := embed_data.get('footer'):
            embed.set_footer(
                text=format_string(footer_data.get('text')),
                icon_url=footer_data.get('icon_url')
            )

        # --- 发送到指定频道 ---
        for channel_id in channel_ids:
            try:
                channel = await self.bot.fetch_channel(channel_id)
                if isinstance(channel, (discord.TextChannel, discord.Thread)):
                    await channel.send(embed=embed)
                else:
                    logger.warning(f"无法在频道 {channel_id} 发送感谢公告，因为它不是一个文本频道或子区。")
            except discord.errors.NotFound:
                logger.warning(f"无法在频道 {channel_id} 发送感谢公告，因为未找到该频道。")
            except discord.errors.Forbidden:
                logger.warning(f"无法在频道 {channel_id} 发送感谢公告，因为机器人缺少权限。")
            except Exception as e:
                logger.error(f"在频道 {channel_id} 发送感谢公告时发生未知错误: {e}", exc_info=True)


def setup(bot):
    bot.add_cog(Donation(bot))