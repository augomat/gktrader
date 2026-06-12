"""Initial GKTrader schema."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from gktrader.db.base import Base
    from gktrader.db import models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind)


def downgrade() -> None:
    from gktrader.db.base import Base
    from gktrader.db import models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.drop_all(bind)
