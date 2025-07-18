import os
import glob
import sqlite3 # <<< 导入标准库 sqlite3
from datetime import datetime, timedelta
import logging
from typing import List, Tuple, Optional

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime,
    ForeignKey, UniqueConstraint, select, func, delete, update
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
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
    claim_cooldown_hours = Column(Integer, nullable=False, default=168)
    coupons = relationship("Coupon", back_populates="project", cascade="all, delete-orphan", passive_deletes=True)
    bans = relationship("Ban", back_populates="project", cascade="all, delete-orphan", passive_deletes=True)

class Coupon(Base):
    __tablename__ = "coupons"
    id = Column(Integer, primary_key=True)
    code = Column(String, nullable=False, unique=True)
    is_claimed = Column(Boolean, nullable=False, default=False)
    claimed_by = Column(Integer)
    claimed_at = Column(DateTime)
    expiry_date = Column(DateTime, nullable=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    project = relationship("Project", back_populates="coupons")

class Ban(Base):
    __tablename__ = "bans"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    banned_until = Column(DateTime, nullable=True)
    reason = Column(String)
    project = relationship("Project", back_populates="bans")
    __table_args__ = (UniqueConstraint("user_id", "project_id", name="_user_project_uc"),)

class DatabaseManager:
    def __init__(self):
        self.engine = async_engine

    async def connect(self):
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
            project_id = None
            if project_name and project_name != "global":
                project = await self.get_project(project_name)
                if not project:
                    return False, f"未找到项目 '{project_name}'。"
                project_id = project.id
            
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
            project_id = None
            if project_name:
                project = await self.get_project(project_name)
                if not project:
                    return False, f"未找到项目 '{project_name}'。"
                project_id = project.id

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
                    return 'NO_PROJECT', None

                ban_check_stmt = select(Ban).filter(
                    Ban.user_id == user_id,
                    (Ban.project_id == project.id) | (Ban.project_id.is_(None))
                )
                ban_result = await session.execute(ban_check_stmt)
                for ban in ban_result.scalars().all():
                    if ban.banned_until is None or ban.banned_until > current_time: # <<< 统一使用 utcnow()
                        return 'BANNED', f"您已被封禁。原因: {ban.reason}"
                
                if not project.is_claim_active:
                    return 'DISABLED', None

                last_claim_stmt = select(Coupon).filter_by(claimed_by=user_id, project_id=project.id).order_by(Coupon.claimed_at.desc()).limit(1)
                last_claim_result = await session.execute(last_claim_stmt)
                last_claim = last_claim_result.scalar_one_or_none()

                if last_claim and last_claim.claimed_at:
                    cooldown_end = last_claim.claimed_at + timedelta(hours=project.claim_cooldown_hours)
                    if current_time < cooldown_end: # <<< 统一使用 utcnow()
                        rem = cooldown_end - current_time
                        h, r = divmod(int(rem.total_seconds()), 3600)
                        m, _ = divmod(r, 60)
                        return 'COOLDOWN', (f"{h}小时 {m}分钟", last_claim.code)

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
                    return 'NO_STOCK', None
                
                coupon_to_claim.is_claimed = True
                coupon_to_claim.claimed_by = user_id
                coupon_to_claim.claimed_at = current_time # <<< 统一使用 utcnow()
                
                return 'SUCCESS', (coupon_to_claim.code, coupon_to_claim.expiry_date)

        logger.error(f"claim_coupon 函数意外地执行到了末尾而没有返回任何值。User: {user_id}, Project: {project_name}")
        return 'ERROR', "数据库处理时发生未知错误，请联系管理员。"

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
        # 使用 /app/backups 确保路径在容器内是绝对且正确的
        backup_dir = '/app/backups'
        os.makedirs(backup_dir, exist_ok=True)
    
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M')
        backup_path = os.path.join(backup_dir, f'coupon_bot_{timestamp}.db')
    
        try:
            # 这是执行SQLite在线备份最安全、最推荐的方式
            async with self.engine.connect() as conn:
                await conn.run_sync(
                    lambda sync_conn: sync_conn.backup(sqlite3.connect(backup_path))
                )
        
            # 清理旧备份（保留最近5个）
            backups = sorted(glob.glob(os.path.join(backup_dir, '*.db')), key=os.path.getmtime)
            if len(backups) > 5:
                for old_backup in backups[:-5]:
                    try:
                        os.remove(old_backup)
                    except OSError as e:
                        logger.error(f"清理旧备份文件 {old_backup} 失败: {e}")

            logger.info(f"数据库已成功备份到 {backup_path}")
            return True
        except Exception as e:
            # 至关重要：使用 exc_info=True 来记录完整的错误堆栈
            logger.error(f"数据库备份失败: {e}", exc_info=True)
            return False

    async def get_coupon_details(self, code: str) -> Optional[Coupon]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Coupon).filter_by(code=code))
            return result.scalar_one_or_none()