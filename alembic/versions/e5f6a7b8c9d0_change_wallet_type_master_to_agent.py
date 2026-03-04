"""change wallet type master to agent

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-03

"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add 'agent' to the enum if it doesn't exist yet
    op.execute("ALTER TYPE wallettype ADD VALUE IF NOT EXISTS 'agent'")

    # Update existing master wallets to agent (only if 'master' exists in the enum)
    conn = op.get_bind()
    result = conn.execute(
        text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM pg_enum "
            "  WHERE enumtypid = 'wallettype'::regtype AND enumlabel = 'master'"
            ")"
        )
    )
    has_master = result.scalar()
    if has_master:
        conn.execute(text("UPDATE wallets SET wallet_type = 'agent' WHERE wallet_type = 'master'"))


def downgrade() -> None:
    op.execute("ALTER TYPE wallettype ADD VALUE IF NOT EXISTS 'master'")
    op.execute("UPDATE wallets SET wallet_type = 'master' WHERE wallet_type = 'agent'")
