"""Simplify risk settings: remove dollar fields, rename max_leverage to leverage

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-03-15 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "k1l2m3n4o5p6"
down_revision: str = "j0k1l2m3n4o5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("risk_settings", "max_leverage", new_column_name="leverage")
    op.drop_column("risk_settings", "max_position_size_usd")
    op.drop_column("risk_settings", "max_daily_loss_usd")


def downgrade() -> None:
    op.add_column(
        "risk_settings",
        op.Column("max_daily_loss_usd", sa.Numeric(12, 2), nullable=False, server_default="500.0"),
    )
    op.add_column(
        "risk_settings",
        op.Column(
            "max_position_size_usd", sa.Numeric(12, 2), nullable=False, server_default="10000.0"
        ),
    )
    op.alter_column("risk_settings", "leverage", new_column_name="max_leverage")
