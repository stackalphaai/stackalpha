"""Add margin_per_trade_percent, drop position_sizing_method and max_position_size_percent

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
        sa.Column(
            "margin_per_trade_percent",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="10.0",
        ),
    )
    op.drop_column("risk_settings", "position_sizing_method")
    op.drop_column("risk_settings", "max_position_size_percent")


def downgrade() -> None:
    op.add_column(
        "risk_settings",
        sa.Column(
            "max_position_size_percent",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="10.0",
        ),
    )
    op.add_column(
        "risk_settings",
        sa.Column(
            "position_sizing_method",
            sa.String(20),
            nullable=False,
            server_default="fixed_percent",
        ),
    )
    op.drop_column("risk_settings", "margin_per_trade_percent")
