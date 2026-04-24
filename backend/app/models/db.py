from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine

logger = logging.getLogger(__name__)


def _build_database_url() -> str:
    """构建数据库连接URL,优先级: DATABASE_URL > MySQL配置 > SQLite"""
    # 1. 直接使用 DATABASE_URL
    explicit_url = os.getenv("DATABASE_URL")
    if explicit_url:
        logger.info(f"使用 DATABASE_URL: {explicit_url.split('@')[-1] if '@' in explicit_url else '***'}")
        return explicit_url

    # 2. 使用 MySQL 配置
    mysql_address = os.getenv("MYSQL_ADDRESS")
    mysql_username = os.getenv("MYSQL_USERNAME")
    mysql_password = os.getenv("MYSQL_PASSWORD")
    mysql_database = os.getenv("MYSQL_DATABASE", "auto_home")

    if mysql_address and mysql_username and mysql_password:
        url = (
            f"mysql+pymysql://{mysql_username}:{mysql_password}"
            f"@{mysql_address}/{mysql_database}?charset=utf8mb4"
        )
        logger.info(f"使用 MySQL 数据库: {mysql_address}/{mysql_database}")
        return url

    # 3. 回退到 SQLite
    logger.warning("⚠ 未配置 MySQL,使用 SQLite 数据库 (仅适合开发/测试)")
    return "sqlite:///./auto_home.db"


DATABASE_URL = _build_database_url()

# 创建引擎,根据数据库类型优化配置
if DATABASE_URL.startswith("sqlite"):
    ENGINE = create_engine(
        DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
else:
    ENGINE = create_engine(
        DATABASE_URL,
        echo=False,
        pool_size=5,  # 连接池大小
        max_overflow=10,  # 最大溢出连接数
        pool_timeout=30,  # 连接超时时间(秒)
        pool_recycle=1800,  # 连接回收时间(秒)
        pool_pre_ping=True,  # 连接前检查有效性
    )


def init_db() -> None:
    """初始化数据库,创建所有表"""
    try:
        SQLModel.metadata.create_all(ENGINE)
        logger.info("✓ 数据库表初始化完成")
    except Exception as e:
        logger.error(f"❌ 数据库初始化失败: {e}")
        raise


@contextmanager
def get_session() -> Iterator[Session]:
    """获取数据库会话(上下文管理器)"""
    session = Session(ENGINE)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

