"""change wallet type master to agent

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-03

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add 'agent' to the enum, update rows, remove 'master'
    # PostgreSQL requires explicit enum manipulation
    op.execute("ALTER TYPE wallettype ADD VALUE IF NOT EXISTS 'agent'")

    # Update existing master wallets to agent
    op.execute("UPDATE wallets SET wallet_type = 'agent' WHERE wallet_type = 'master'")


def downgrade() -> None:
    # Revert agent wallets back to master
    op.execute("UPDATE wallets SET wallet_type = 'master' WHERE wallet_type = 'agent'")
