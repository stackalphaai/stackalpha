"""add risk_settings table

Revision ID: a1b2c3d4e5f6
Revises: fcbf6c2fcc92
Create Date: 2026-02-13 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "fcbf6c2fcc92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Position Sizing
        sa.Column(
            "position_sizing_method",
            sa.Enum(
                "fixed_amount",
                "fixed_percent",
                "kelly",
                "risk_parity",
                name="positionsizingmethod",
            ),
            nullable=False,
        ),
        sa.Column("max_position_size_usd", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("max_position_size_percent", sa.Numeric(precision=5, scale=2), nullable=False),
        # Portfolio Limits
        sa.Column("max_portfolio_heat", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("max_open_positions", sa.Integer(), nullable=False),
        sa.Column("max_leverage", sa.Integer(), nullable=False),
        # Drawdown Limits
        sa.Column("max_daily_loss_usd", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("max_daily_loss_percent", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("max_weekly_loss_percent", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("max_monthly_loss_percent", sa.Numeric(precision=5, scale=2), nullable=False),
        # Risk-Reward
        sa.Column("min_risk_reward_ratio", sa.Numeric(precision=4, scale=2), nullable=False),
        # Diversification
        sa.Column("max_correlated_positions", sa.Integer(), nullable=False),
        sa.Column(
            "max_single_asset_exposure_percent", sa.Numeric(precision=5, scale=2), nullable=False
        ),
        # Circuit Breakers
        sa.Column("max_consecutive_losses", sa.Integer(), nullable=False),
        sa.Column("trading_paused", sa.Boolean(), nullable=False),
        # Auto-Trading Features
        sa.Column("enable_trailing_stop", sa.Boolean(), nullable=False),
        sa.Column("trailing_stop_percent", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("enable_scale_out", sa.Boolean(), nullable=False),
        sa.Column("enable_pyramiding", sa.Boolean(), nullable=False),
        sa.Column("min_signal_confidence", sa.Numeric(precision=3, scale=2), nullable=False),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("risk_settings")
    sa.Enum(name="positionsizingmethod").drop(op.get_bind(), checkfirst=True)
