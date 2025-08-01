import os
import glob
import aiosqlite
from datetime import datetime, timedelta
import logging
from typing import List, Tuple, Optional, Set

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime,
    ForeignKey, UniqueConstraint, select, func, delete, update, text
)
from constants import (
    DEFAULT_COOLDOWN_HOURS,
    CLAIM_STATUS_SUCCESS, CLAIM_STATUS_BANNED, CLAIM_STATUS_DISABLED,
    CLAIM_STATUS_COOLDOWN, CLAIM_STATUS_NO_STOCK, CLAIM_STATUS_NO_PROJECT,
    CLAIM_STATUS_ERROR
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, selectinload
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger('database')

# 定义容器内数据存储的绝对路径
DATA_DIR = "/app/data"
DB_FILE = "coupon_bot.db"
DB_PATH = os.path.join(DATA_DIR, DB_FILE)

# 在启动时确保数据目录存在
os.makedirs(DATA_DIR, exist_ok=True)

DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"
Base = declarative_base()
async_engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(
    bind=async_engine, class_=AsyncSession, expire_on_commit=False
)

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    is_claim_active = Column(Boolean, nullable=False, default=True)
    claim_cooldown_hours = Column(Integer, nullable=False, default=DEFAULT_COOLDOWN_HOURS)
    coupons = relationship("Coupon", back_populates="project", cascade="all, delete-orphan", passive_deletes=True)
    bans = relationship("Ban", back_populates="project", cascade="all, delete-orphan", passive_deletes=True)

class Coupon(Base):
    __tablename__ = "coupons"
    id = Column(Integer, primary_key=True)
    code = Column(String, nullable=False, unique=True)
    is_claimed = Column(Boolean, nullable=False, default=False, index=True)
    claimed_by = Column(Integer, index=True)
    claimed_at = Column(DateTime)
    cooldown_expires_at = Column(DateTime, nullable=True) # <<< 新增字段
    expiry_date = Column(DateTime, nullable=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    project = relationship("Project", back_populates="coupons")

class Ban(Base):
    __tablename__ = "bans"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    banned_until = Column(DateTime, nullable=True)
    reason = Column(String)
    project = relationship("Project", back_populates="bans")
    __table_args__ = (UniqueConstraint("user_id", "project_id", name="_user_project_uc"),)

class GeminiKey(Base):
    __tablename__ = "gemini_keys"
    id = Column(Integer, primary_key=True)
    key = Column(String, nullable=False, unique=True, index=True)
    donator_user_id = Column(Integer, nullable=False, index=True)
    donated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True)
    last_used_at = Column(DateTime)

class DonationLog(Base):
    __tablename__ = "donation_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    user_name = Column(String, nullable=False)
    user_display_name = Column(String, nullable=False)
    donated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    quantity_new = Column(Integer, nullable=False, default=0)
    quantity_duplicate = Column(Integer, nullable=False, default=0)
    file_path = Column(String, nullable=True)

class DatabaseManager:
    def __init__(self, config: dict):
        self.engine = async_engine
        self.config = config

    def _run_migrations(self, connection):
        """
        执行手动的数据库迁移。这是一个同步函数，由 run_sync 调用。
        """
        # 使用传入的 SQLAlchemy 连接来执行原生 SQL
        cursor = connection.execute(text("PRAGMA table_info(coupons)"))
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'cooldown_expires_at' not in columns:
            logger.info("数据库迁移：正在为 'coupons' 表添加 'cooldown_expires_at' 列...")
            connection.execute(text("ALTER TABLE coupons ADD COLUMN cooldown_expires_at DATETIME"))
            logger.info("数据库迁移成功。")

    async def connect(self):
        # 首先，运行迁移脚本来更新现有表的结构
        async with self.engine.begin() as conn:
            await conn.run_sync(self._run_migrations)
        
        # 然后，创建任何尚不存在的表
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def create_project(self, name: str) -> Tuple[bool, str]:
        async with AsyncSessionLocal() as session:
            try:
                new_project = Project(name=name)
                session.add(new_project)
                await session.commit()
                return True, f"项目 '{name}' 已成功创建。"
            except IntegrityError:
                await session.rollback()
                return False, f"名为 '{name}' 的项目已存在。"

    async def get_project(self, project_name: str) -> Optional[Project]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Project).filter_by(name=project_name))
            return result.scalar_one_or_none()

    async def _get_project_id_by_name(self, session: AsyncSession, project_name: str) -> Optional[int]:
        """辅助函数：根据项目名称获取项目ID。"""
        if not project_name or project_name == "global":
            return None
        stmt = select(Project.id).filter_by(name=project_name)
        result = await session.execute(stmt)
        project_id = result.scalar_one_or_none()
        if project_id is None:
            raise ValueError(f"未找到项目 '{project_name}'。")
        return project_id

    async def get_all_project_names(self) -> List[str]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Project.name).order_by(Project.name))
            return result.scalars().all()

    async def set_project_setting(self, project_name: str, key: str, value) -> bool:
        # 优化：动态检查key是否是Project模型的有效属性，而不是硬编码
        if not hasattr(Project, key):
            logger.warning(f"尝试设置一个不存在的项目属性: '{key}'")
            return False
            
        async with AsyncSessionLocal() as session:
            stmt = update(Project).where(Project.name == project_name).values({key: value})
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def delete_project(self, project_name: str) -> Tuple[bool, str]:
        # 优化：在单个会话中完成查询和删除，避免调用 self.get_project 导致的额外查询
        async with AsyncSessionLocal() as session:
            async with session.begin():
                stmt = select(Project).filter_by(name=project_name)
                result = await session.execute(stmt)
                project_to_delete = result.scalar_one_or_none()

                if not project_to_delete:
                    return False, f"未找到项目 '{project_name}'。"
                
                # SQLAlchemy 会自动处理通过 relationship 设置的 cascade delete
                await session.delete(project_to_delete)
            return True, f"项目 '{project_name}' 及其所有关联数据已被成功删除。"

    # <<< 封禁函数的逻辑已完全修正
    async def ban_user(self, user_id: int, project_name: Optional[str], reason: str, duration_hours: Optional[int]) -> Tuple[bool, str]:
        async with AsyncSessionLocal() as session:
            try:
                project_id = await self._get_project_id_by_name(session, project_name)
            except ValueError as e:
                return False, str(e)

            # 根据时长计算到期时间，或设为永久
            banned_until = (datetime.utcnow() + timedelta(hours=duration_hours)) if duration_hours else None
            
            stmt = select(Ban).filter_by(user_id=user_id, project_id=project_id)
            result = await session.execute(stmt)
            existing_ban = result.scalar_one_or_none()

            if existing_ban:
                existing_ban.banned_until = banned_until
                existing_ban.reason = reason
                message = "用户的封禁已被更新。"
            else:
                new_ban = Ban(user_id=user_id, project_id=project_id, banned_until=banned_until, reason=reason)
                session.add(new_ban)
                message = "用户已被成功封禁。"

            await session.commit()
            
            scope_text = f"项目 '{project_name}'" if project_name else "全局"
            duration_text = f"{duration_hours} 小时" if duration_hours else "永久"
            full_message = f"{message} 范围: {scope_text}, 时长: {duration_text}。"
            return True, full_message

    async def unban_user(self, user_id: int, project_name: Optional[str]) -> Tuple[bool, str]:
        async with AsyncSessionLocal() as session:
            try:
                project_id = await self._get_project_id_by_name(session, project_name)
            except ValueError as e:
                return False, str(e)

            stmt = delete(Ban).filter_by(user_id=user_id, project_id=project_id)
            result = await session.execute(stmt)
            await session.commit()
            
            scope_text = f"在项目 '{project_name}' 中" if project_name else "全局"
            if result.rowcount > 0:
                return True, f"用户已成功从 {scope_text} 解封。"
            else:
                return False, f"该用户未被 {scope_text} 封禁。"

    async def add_coupons(self, project_name: str, codes: List[str], expiry_days: Optional[int] = None) -> Optional[Tuple[int, int, List[str]]]:
        async with AsyncSessionLocal() as session:
            project = await self.get_project(project_name)
            if not project:
                return None
            
            existing_codes_stmt = select(Coupon.code).where(Coupon.code.in_(codes))
            result = await session.execute(existing_codes_stmt)
            existing_codes = set(result.scalars().all())

            expiry_date = (datetime.utcnow() + timedelta(days=expiry_days)) if expiry_days else None
            new_coupons = [
                Coupon(code=code, project_id=project.id, expiry_date=expiry_date)
                for code in codes if code not in existing_codes
            ]

            if new_coupons:
                session.add_all(new_coupons)
                await session.commit()

            return len(new_coupons), len(codes) - len(new_coupons), [c.code for c in new_coupons]

    async def get_stock(self, project_name: str) -> Optional[int]:
        async with AsyncSessionLocal() as session:
            project = await self.get_project(project_name)
            if not project:
                return None
            
            current_time = datetime.utcnow()
            stmt = select(func.count(Coupon.id)).where(
                Coupon.project_id == project.id,
                Coupon.is_claimed == False,
                (Coupon.expiry_date.is_(None) | (Coupon.expiry_date > current_time)))
            result = await session.execute(stmt)
            return result.scalar_one()

    async def claim_coupon(self, user_id: int, project_name: str) -> Tuple[str, Optional[any]]:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                current_time = datetime.utcnow() # <<< 统一使用 utcnow()

                project_result = await session.execute(select(Project).filter_by(name=project_name))
                project = project_result.scalar_one_or_none()
                if not project:
                    return CLAIM_STATUS_NO_PROJECT, None

                ban_check_stmt = select(Ban).filter(
                    Ban.user_id == user_id,
                    (Ban.project_id == project.id) | (Ban.project_id.is_(None))
                )
                ban_result = await session.execute(ban_check_stmt)
                for ban in ban_result.scalars().all():
                    if ban.banned_until is None or ban.banned_until > current_time: # <<< 统一使用 utcnow()
                        return CLAIM_STATUS_BANNED, f"您已被封禁。原因: {ban.reason}"
                
                if not project.is_claim_active:
                    return CLAIM_STATUS_DISABLED, None

                last_claim_stmt = select(Coupon).filter_by(claimed_by=user_id, project_id=project.id).order_by(Coupon.claimed_at.desc()).limit(1)
                last_claim_result = await session.execute(last_claim_stmt)
                last_claim = last_claim_result.scalar_one_or_none()

                if last_claim and last_claim.cooldown_expires_at and current_time < last_claim.cooldown_expires_at:
                    rem = last_claim.cooldown_expires_at - current_time
                    h, r = divmod(int(rem.total_seconds()), 3600)
                    m, _ = divmod(r, 60)
                    return CLAIM_STATUS_COOLDOWN, (f"{h}小时 {m}分钟", last_claim.code)

                # 优化说明：
                # 这里的 SELECT -> UPDATE 模式在 SQLite 中是安全的，因为 aiosqlite 在执行事务时
                # 会对整个数据库文件加锁，从而防止了并发场景下的竞态条件。
                # 然而，如果未来将数据库后端更换为 PostgreSQL 或 MySQL 等支持行级锁的系统，
                # 此处可能需要改为使用 `SELECT ... FOR UPDATE` 或更复杂的原子操作来保证并发安全。
                claimable_coupon_stmt = (
                    select(Coupon)
                    .where(
                       Coupon.project_id == project.id,
                       Coupon.is_claimed == False,
                       (Coupon.expiry_date.is_(None) | (Coupon.expiry_date > current_time)),
                   )
                   .order_by(Coupon.expiry_date.asc())
                   .limit(1)
               )
                
                coupon_result = await session.execute(claimable_coupon_stmt)
                coupon_to_claim = coupon_result.scalar_one_or_none()

                if not coupon_to_claim:
                    return CLAIM_STATUS_NO_STOCK, None
                
                # --- 固化冷却时间 ---
                cooldown_end_time = current_time + timedelta(hours=project.claim_cooldown_hours)

                coupon_to_claim.is_claimed = True
                coupon_to_claim.claimed_by = user_id
                coupon_to_claim.claimed_at = current_time
                coupon_to_claim.cooldown_expires_at = cooldown_end_time # <<< 存储固化的冷却截止时间
                
                return CLAIM_STATUS_SUCCESS, (coupon_to_claim.code, coupon_to_claim.expiry_date)

        logger.error(f"claim_coupon 函数意外地执行到了末尾而没有返回任何值。User: {user_id}, Project: {project_name}")
        return CLAIM_STATUS_ERROR, "数据库处理时发生未知错误，请联系管理员。"

    async def cleanup_expired_coupons(self) -> int:
        """清理所有过期的兑换券"""
        async with AsyncSessionLocal() as session:
            current_time = datetime.utcnow()
            stmt = delete(Coupon).where(
                Coupon.expiry_date.is_not(None),
                Coupon.expiry_date <= current_time
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    async def backup_database(self) -> bool:
        """执行数据库备份"""
        backup_dir = '/app/backups'
        os.makedirs(backup_dir, exist_ok=True)

        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M')
        backup_path = os.path.join(backup_dir, f'coupon_bot_{timestamp}.db')

        try:
            # 直接使用 aiosqlite 进行在线备份，绕过 SQLAlchemy 的复杂性
            async with aiosqlite.connect(DB_PATH) as src_db, \
                       aiosqlite.connect(backup_path) as dest_db:
                await src_db.backup(dest_db)

            # 从配置中读取要保留的备份数量
            retention_count = self.config.get('tasks', {}).get('backup_retention_count', 5)

            # 清理旧备份
            backups = sorted(glob.glob(os.path.join(backup_dir, '*.db')), key=os.path.getmtime)
            if len(backups) > retention_count:
                for old_backup in backups[:-retention_count]:
                    try:
                        os.remove(old_backup)
                    except OSError as e:
                        logger.error(f"清理旧备份文件 {old_backup} 失败: {e}")

            logger.info(f"数据库已成功备份到 {backup_path}")
            return True
        except Exception as e:
            logger.error(f"数据库备份失败: {e}", exc_info=True)
            return False

    async def add_gemini_keys(self, keys: List[str], donator_user_id: int) -> Tuple[int, int]:
        """
        向数据库批量添加 Gemini 密钥，并自动处理重复。
        :param keys: 要添加的密钥列表。
        :param donator_user_id: 捐赠者的用户ID。
        :return: 一个元组 (新增数量, 重复数量)。
        """
        async with AsyncSessionLocal() as session:
            # 找出数据库中已经存在的 keys
            existing_keys_stmt = select(GeminiKey.key).where(GeminiKey.key.in_(keys))
            result = await session.execute(existing_keys_stmt)
            existing_keys = set(result.scalars().all())

            new_unique_keys = set(keys) - existing_keys
            
            if not new_unique_keys:
                return 0, len(existing_keys)

            new_keys_to_add = [
                GeminiKey(key=key, donator_user_id=donator_user_id)
                for key in new_unique_keys
            ]

            session.add_all(new_keys_to_add)
            await session.commit()

            return len(new_keys_to_add), len(existing_keys)

    async def get_coupon_details(self, code: str) -> Optional[Coupon]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Coupon).filter_by(code=code))
            return result.scalar_one_or_none()

    async def log_donation(self, user_id: int, user_name: str, user_display_name: str, quantity_new: int, quantity_duplicate: int, file_path: str):
        """记录一次捐赠事件。"""
        async with AsyncSessionLocal() as session:
            log_entry = DonationLog(
                user_id=user_id,
                user_name=user_name,
                user_display_name=user_display_name,
                quantity_new=quantity_new,
                quantity_duplicate=quantity_duplicate,
                file_path=file_path
            )
            session.add(log_entry)
            await session.commit()

    async def check_existing_codes(self, codes: List[str]) -> Set[str]:
        """检查给定的代码列表中哪些已经存在于数据库中。"""
        if not codes:
            return set()
        async with AsyncSessionLocal() as session:
            stmt = select(Coupon.code).where(Coupon.code.in_(codes))
            result = await session.execute(stmt)
            return set(result.scalars().all())

    async def get_claims_by_user(self, user_id: int, limit: int = 3) -> List[Coupon]:
        """获取一个用户最近的领取记录。"""
        async with AsyncSessionLocal() as session:
            stmt = (
                select(Coupon)
                .options(selectinload(Coupon.project)) # 预加载项目信息以避免N+1查询
                .filter_by(claimed_by=user_id)
                .order_by(Coupon.claimed_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return result.scalars().all()