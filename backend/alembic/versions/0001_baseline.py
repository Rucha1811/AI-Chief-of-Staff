"""baseline

Revision ID: 0001
Revises:
Create Date: 2026-09-13 14:53:50.917665

No-op baseline that represents the database state as it existed before
productization. It intentionally does NOT reconcile pre-existing type
drift between the models and the live schema:

  - emails.from_email: DB VARCHAR(512) vs model String(256)
  - emails.from_name : DB VARCHAR(512) vs model String(256)
  - emails.to_email  : DB VARCHAR(8192) vs model String(256)

Narrowing to_email would risk truncating existing values, so the drift is
left untouched here and should be resolved in a dedicated later revision
(by widening the model columns).
"""
from typing import Sequence, Union


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass