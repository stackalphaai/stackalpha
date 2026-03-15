"""Update risk settings defaults to match admin config

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-03-15 14:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j0k1l2m3n4o5"
down_revision: str | None = "i9j0k1l2m3n4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Update existing users with the old hardcoded defaults to match admin config:
    # min_signal_confidence: 0.7 -> 0.6 (matches llm_min_confidence)
    # min_risk_reward_ratio: 1.5 -> 1.2 (matches llm_min_risk_reward_ratio)
    op.execute(
        "UPDATE risk_settings SET min_signal_confidence = 0.6 WHERE min_signal_confidence = 0.7"
    )
    op.execute(
        "UPDATE risk_settings SET min_risk_reward_ratio = 1.2 WHERE min_risk_reward_ratio = 1.5"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE risk_settings SET min_signal_confidence = 0.7 WHERE min_signal_confidence = 0.6"
    )
    op.execute(
        "UPDATE risk_settings SET min_risk_reward_ratio = 1.5 WHERE min_risk_reward_ratio = 1.2"
    )
