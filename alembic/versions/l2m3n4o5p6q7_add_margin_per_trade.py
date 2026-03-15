"""Add margin_per_trade to risk_settings

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-03-15 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "l2m3n4o5p6q7"
down_revision: str = "k1l2m3n4o5p6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "risk_settings",
        sa.Column("margin_per_trade", sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("risk_settings", "margin_per_trade")
