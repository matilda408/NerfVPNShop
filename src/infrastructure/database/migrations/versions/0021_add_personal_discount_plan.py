from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("personal_discount_plan_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_users_personal_discount_plan_id",
        "users",
        ["personal_discount_plan_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_users_personal_discount_plan_id",
        "users",
        "plans",
        ["personal_discount_plan_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_personal_discount_plan_id", "users", type_="foreignkey")
    op.drop_index("ix_users_personal_discount_plan_id", table_name="users")
    op.drop_column("users", "personal_discount_plan_id")
