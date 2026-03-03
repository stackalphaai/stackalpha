"""add binance exchange support

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-01

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create exchange_connections table
    op.create_table(
        "exchange_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("exchange_type", sa.String(20), nullable=False),
        sa.Column("label", sa.String(100), nullable=True),
        sa.Column("encrypted_api_key", sa.Text(), nullable=True),
        sa.Column("encrypted_api_secret", sa.Text(), nullable=True),
        sa.Column("is_testnet", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "is_trading_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("balance_usd", sa.Numeric(20, 8), nullable=True),
        sa.Column("margin_used", sa.Numeric(20, 8), nullable=True),
        sa.Column("unrealized_pnl", sa.Numeric(20, 8), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id", "exchange_type", "is_testnet", name="uq_user_exchange_testnet"
        ),
    )

    # Add exchange column to signals
    op.add_column(
        "signals",
        sa.Column(
            "exchange",
            sa.String(20),
            nullable=False,
            server_default="hyperliquid",
        ),
    )
    op.create_index("ix_signals_exchange", "signals", ["exchange"])

    # Add exchange and related columns to trades
    op.add_column(
        "trades",
        sa.Column(
            "exchange",
            sa.String(20),
            nullable=False,
            server_default="hyperliquid",
        ),
    )
    op.create_index("ix_trades_exchange", "trades", ["exchange"])

    op.add_column(
        "trades",
        sa.Column(
            "exchange_connection_id",
            sa.String(36),
            sa.ForeignKey("exchange_connections.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )

    op.add_column(
        "trades",
        sa.Column("exchange_order_id", sa.String(200), nullable=True),
    )
    op.add_column(
        "trades",
        sa.Column("tp_order_id", sa.String(200), nullable=True),
    )
    op.add_column(
        "trades",
        sa.Column("sl_order_id", sa.String(200), nullable=True),
    )

    # Make wallet_id nullable (Binance trades use exchange_connection_id instead)
    op.alter_column("trades", "wallet_id", existing_type=sa.String(36), nullable=True)


def downgrade() -> None:
    # Make wallet_id non-nullable again
    op.alter_column("trades", "wallet_id", existing_type=sa.String(36), nullable=False)

    # Drop trade columns
    op.drop_column("trades", "sl_order_id")
    op.drop_column("trades", "tp_order_id")
    op.drop_column("trades", "exchange_order_id")
    op.drop_column("trades", "exchange_connection_id")
    op.drop_index("ix_trades_exchange", "trades")
    op.drop_column("trades", "exchange")

    # Drop signal column
    op.drop_index("ix_signals_exchange", "signals")
    op.drop_column("signals", "exchange")

    # Drop exchange_connections table
    op.drop_table("exchange_connections")
