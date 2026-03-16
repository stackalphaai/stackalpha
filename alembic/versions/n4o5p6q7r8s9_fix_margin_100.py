"""Fix margin_per_trade_percent = 100 for users who got wrong default

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-03-16 13:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "n4o5p6q7r8s9"
down_revision: str = "m3n4o5p6q7r8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Any user with margin_per_trade_percent >= 100 got the wrong default
    # Reset them to 10% (the intended default)
    op.execute(
        "UPDATE risk_settings SET margin_per_trade_percent = 10.0 "
        "WHERE margin_per_trade_percent >= 100"
    )


def downgrade() -> None:
    pass
