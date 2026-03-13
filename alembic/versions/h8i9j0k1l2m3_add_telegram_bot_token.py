"""add encrypted_bot_token to telegram_connections

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-03-13

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h8i9j0k1l2m3"
down_revision: str | None = "g7h8i9j0k1l2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_connections",
        sa.Column("encrypted_bot_token", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("telegram_connections", "encrypted_bot_token")
