import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.database_url,
    echo=settings.app_debug,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Kiểm tra kết nối DB và báo revision schema hiện tại.

    **Không còn `Base.metadata.create_all`.** Schema do Alembic quản lý
    (`alembic upgrade head`). Lý do bỏ: `create_all` chỉ TẠO bảng chưa tồn tại,
    nó không bao giờ ALTER — thêm một cột vào model thì máy dev (DB trống) chạy ngon
    còn staging (bảng đã có) im lặng không đổi gì, rồi vỡ lúc runtime với
    `column ... does not exist`. Không cảnh báo, không rollback, không biết môi
    trường nào đang ở version nào.
    """
    async with engine.connect() as conn:
        try:
            revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        except Exception:
            logger.warning(
                "Chưa có bảng alembic_version — schema chưa được migrate. "
                "Chạy: alembic upgrade head"
            )
            return

    if revision is None:
        logger.warning("alembic_version rỗng — chạy: alembic upgrade head")
    else:
        logger.info("Schema đang ở revision %s", revision)
