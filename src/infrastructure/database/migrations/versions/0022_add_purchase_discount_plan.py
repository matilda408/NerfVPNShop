from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("purchase_discount_plan_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_users_purchase_discount_plan_id",
        "users",
        ["purchase_discount_plan_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_users_purchase_discount_plan_id",
        "users",
        "plans",
        ["purchase_discount_plan_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_purchase_discount_plan_id", "users", type_="foreignkey")
    op.drop_index("ix_users_purchase_discount_plan_id", table_name="users")
    op.drop_column("users", "purchase_discount_plan_id")
