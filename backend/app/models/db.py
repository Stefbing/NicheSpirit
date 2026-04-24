from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine


def _build_database_url() -> str:
    explicit_url = os.getenv("DATABASE_URL")
    if explicit_url:
        return explicit_url

    mysql_address = os.getenv("MYSQL_ADDRESS")
    mysql_username = os.getenv("MYSQL_USERNAME")
    mysql_password = os.getenv("MYSQL_PASSWORD")

    if mysql_address and mysql_username and mysql_password:
        return (
            f"mysql+pymysql://{mysql_username}:{mysql_password}"
            f"@{mysql_address}/auto_home?charset=utf8mb4"
        )

    return "sqlite:///./auto_home.db"


DATABASE_URL = _build_database_url()
ENGINE = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)


def init_db() -> None:
    SQLModel.metadata.create_all(ENGINE)


@contextmanager
def get_session() -> Iterator[Session]:
    with Session(ENGINE) as session:
        yield session

