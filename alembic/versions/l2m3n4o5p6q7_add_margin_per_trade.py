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
    conn = op.get_bind()

    # Add margin_per_trade_percent if not exists
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='risk_settings' AND column_name='margin_per_trade_percent'"
        )
    )
    if not result.fetchone():
        op.add_column(
            "risk_settings",
            sa.Column(
                "margin_per_trade_percent",
                sa.Numeric(5, 2),
                nullable=False,
                server_default="10.0",
            ),
        )

    # Drop position_sizing_method if exists
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='risk_settings' AND column_name='position_sizing_method'"
        )
    )
    if result.fetchone():
        op.drop_column("risk_settings", "position_sizing_method")

    # Drop max_position_size_percent if exists
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='risk_settings' AND column_name='max_position_size_percent'"
        )
    )
    if result.fetchone():
        op.drop_column("risk_settings", "max_position_size_percent")

    # Drop margin_per_trade (old nullable dollar column) if exists
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='risk_settings' AND column_name='margin_per_trade'"
        )
    )
    if result.fetchone():
        op.drop_column("risk_settings", "margin_per_trade")


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
