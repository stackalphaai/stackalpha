"""add risk_percent_per_trade to risk_settings

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "risk_settings",
        sa.Column(
            "risk_percent_per_trade",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="2.0",
        ),
    )


def downgrade() -> None:
    op.drop_column("risk_settings", "risk_percent_per_trade")
