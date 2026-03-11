"""add circuit breaker columns to risk_settings

Revision ID: g7h8i9j0k1l2
Revises: f6a7b8c9d0e1
Create Date: 2026-03-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g7h8i9j0k1l2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "risk_settings",
        sa.Column(
            "circuit_breaker_status",
            sa.String(20),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "risk_settings",
        sa.Column("paused_reason", sa.String(255), nullable=True),
    )
    op.add_column(
        "risk_settings",
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "risk_settings",
        sa.Column("paused_by", sa.String(50), nullable=True),
    )
    op.add_column(
        "risk_settings",
        sa.Column("auto_resume_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("risk_settings", "auto_resume_at")
    op.drop_column("risk_settings", "paused_by")
    op.drop_column("risk_settings", "paused_at")
    op.drop_column("risk_settings", "paused_reason")
    op.drop_column("risk_settings", "circuit_breaker_status")
