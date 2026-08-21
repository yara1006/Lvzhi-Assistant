"""baseline：表结构由队友提供的 SQL 初始化，此处仅占位 revision。

Revision ID: 001_baseline
Revises:
Create Date: 2026-03-26

后续增量变更可在此目录追加迁移脚本。

数据库表结构由 `sql/init.sql` 管理。
运行 `mysql -u root -p < sql/init.sql` 初始化数据库。

启用 Alembic 迁移：
1. 运行：alembic revision --autogenerate -m "描述变更"
2. 运行：alembic upgrade head

"""

from typing import Sequence, Union

from alembic import op

revision: str = "001_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
