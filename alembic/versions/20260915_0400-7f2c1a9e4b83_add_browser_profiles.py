"""add browser_profiles

Bảng này tới từ nhánh `feat/browser-profile-cloak` (đã merge vào main) nhưng chưa có
migration kèm theo — bên đó schema còn được `Base.metadata.create_all` dựng hộ. Nhánh
này quản schema hoàn toàn bằng Alembic (xem `app/database.py:init_db`), nên thiếu
migration là bảng không tồn tại và mọi endpoint `/api/v1/browser/*` sẽ lỗi lúc chạy.

Revision ID: 7f2c1a9e4b83
Revises: 3e20fc8cf23c
Create Date: 2026-09-15 04:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7f2c1a9e4b83'
down_revision: Union[str, None] = '3e20fc8cf23c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'browser_profiles',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('fingerprint_seed', sa.Integer(), nullable=False),
        sa.Column('proxy_url', sa.String(length=500), nullable=True),
        sa.Column('profile_dir', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        # Cùng seed = cùng fingerprint. Trùng seed giữa hai profile nghĩa là hai
        # "máy" khác nhau lại có chung canvas/WebGL — đúng thứ anti-bot dùng để
        # gom chúng về một danh tính, nên chặn ngay ở tầng DB.
        sa.UniqueConstraint('fingerprint_seed', name='uq_browser_profiles_fingerprint_seed'),
    )


def downgrade() -> None:
    op.drop_table('browser_profiles')
