"""seed crawl_source_policies cho amazon · alibaba_1688 · taobao

Giá trị lấy từ bảng khuyến nghị ở `docs/DEV-Design-Crawl-Engine.md` §5 và **cố tình
thận trọng**: bắt đầu chậm rồi nới ra rẻ hơn nhiều so với bắt đầu nhanh rồi bị chặn —
lệnh cấm thường theo dải IP nên mất cả pool chứ không mất một IP.

Vì sao phải seed thay vì để engine dùng default chung: nhịp mặc định 1500ms là quá
nhanh với Amazon và nhanh gấp 4 lần mức an toàn của hai sàn Alibaba. Không có ba dòng
này thì lần chạy thật đầu tiên đã đi sai nhịp.

`tier_chain` cố tình để NULL. `app/sources/registry.py` đã khai đúng thứ tự (vendor
trước, browser sau) và engine dùng thứ tự đó khi cột này rỗng; seed thêm một danh sách
tên tier chỉ tạo thêm một chỗ để gõ sai — gõ sai một ký tự là chuỗi adapter rỗng và
nguồn ngừng hoạt động trong im lặng. Khi nào vận hành cần đổi đường đi (ví dụ tắt hẳn
tier browser để khỏi bị chặn) thì UPDATE cột này, vẫn không cần deploy.

Revision ID: 9b4d2e7c1a05
Revises: 7f2c1a9e4b83
Create Date: 2026-09-15 06:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9b4d2e7c1a05'
down_revision: Union[str, None] = '7f2c1a9e4b83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_POLICIES = [
    # Amazon: chặn theo dải IP rất nhanh → 2 luồng, nhịp 4s (docs §5).
    # Cache 6h: giá đối thủ có đổi trong ngày, nhưng không đổi theo phút.
    {
        "source": "amazon",
        "max_concurrency": 2,
        "min_delay_ms": 4000,
        "jitter_ms": 1000,
        "identity_max_requests": 40,
        "cache_ttl_seconds": 21600,
    },
    # 1688 / Taobao: khắt khe nhất nhóm → 1 luồng, nhịp 6s.
    # Cache 24h: listing nguồn hàng đổi chậm hơn nhiều so với giá bán lẻ (docs §9.3).
    {
        "source": "alibaba_1688",
        "max_concurrency": 1,
        "min_delay_ms": 6000,
        "jitter_ms": 1500,
        "identity_max_requests": 25,
        "cache_ttl_seconds": 86400,
    },
    {
        "source": "taobao",
        "max_concurrency": 1,
        "min_delay_ms": 6000,
        "jitter_ms": 1500,
        "identity_max_requests": 25,
        "cache_ttl_seconds": 86400,
    },
]

_INSERT = sa.text(
    """
    INSERT INTO crawl_source_policies (
        source, max_concurrency, min_delay_ms, jitter_ms, max_attempts,
        identity_max_requests, soft_block_cooldown_s, respect_retry_after,
        cache_ttl_seconds, enabled
    ) VALUES (
        :source, :max_concurrency, :min_delay_ms, :jitter_ms, 2,
        :identity_max_requests, 3600, true,
        :cache_ttl_seconds, true
    )
    ON CONFLICT (source) DO NOTHING
    """
)
"""`DO NOTHING` chứ không `DO UPDATE`: chạy lại migration trên môi trường mà người
vận hành đã tinh chỉnh policy bằng tay thì không được ghi đè công của họ."""


def upgrade() -> None:
    conn = op.get_bind()
    for policy in _POLICIES:
        conn.execute(_INSERT, policy)


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "DELETE FROM crawl_source_policies "
            "WHERE source IN ('amazon', 'alibaba_1688', 'taobao')"
        )
    )
