"""Fix margin_per_trade_percent defaults for existing users

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-03-16 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "m3n4o5p6q7r8"
down_revision: str = "l2m3n4o5p6q7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    # Ensure margin_per_trade_percent column exists
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

    # Update any rows where margin_per_trade_percent is NULL or 0
    op.execute(
        "UPDATE risk_settings SET margin_per_trade_percent = 10.0 "
        "WHERE margin_per_trade_percent IS NULL OR margin_per_trade_percent = 0"
    )

    # Drop legacy columns if they still exist
    for col in [
        "position_sizing_method",
        "max_position_size_percent",
        "margin_per_trade",
        "max_position_size_usd",
        "max_daily_loss_usd",
    ]:
        result = conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='risk_settings' AND column_name=:col"
            ),
            {"col": col},
        )
        if result.fetchone():
            op.drop_column("risk_settings", col)

    # Rename max_leverage to leverage if still using old name
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='risk_settings' AND column_name='max_leverage'"
        )
    )
    if result.fetchone():
        op.alter_column("risk_settings", "max_leverage", new_column_name="leverage")

    # Drop the PostgreSQL enum type if it exists (from position_sizing_method)
    conn.execute(sa.text("DROP TYPE IF EXISTS positionsizingmethod"))


def downgrade() -> None:
    pass  # No downgrade — this is a data fix
