"""Cấu hình Alembic cho engine async (asyncpg).

Hai điểm khác bản template mặc định:

  1. **URL lấy từ `app.config.settings`**, không từ `alembic.ini`. Chuỗi kết nối chỉ
     khai báo một nơi (biến môi trường `DATABASE_URL`), nên container dùng host `db`
     còn máy dev dùng `localhost` mà không phải sửa file nào.

  2. **Engine async**: `run_sync()` để Alembic (vốn đồng bộ) chạy được trên asyncpg.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.database import Base

# Nạp toàn bộ model để `Base.metadata` đầy đủ trước khi autogenerate so sánh.
# Thiếu import nào là bảng đó bị coi như "đã bị xoá" và migration sẽ DROP nó.
import app.models  # noqa: F401  (side-effect import — xem app/models/__init__.py)

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Không có 2 cái này thì đổi kiểu cột / đổi default sẽ bị autogenerate bỏ qua
        # im lặng — đúng loại bug mà migration sinh ra để phòng.
        compare_type=True,
        compare_server_default=True,
    )


def run_migrations_offline() -> None:
    """Sinh SQL ra stdout thay vì chạy — dùng khi DBA muốn review trước."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
