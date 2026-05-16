from typing import Optional

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.core.enums import Locale, Role

from .base import BaseSql
from .timestamp import TimestampMixin


class User(BaseSql, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True, unique=True)

    username: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    referral_code: Mapped[str] = mapped_column(String(64), index=True, unique=True)

    name: Mapped[str] = mapped_column(String(128))
    role: Mapped[Role] = mapped_column(index=True)
    language: Mapped[Locale]

    personal_discount: Mapped[int]
    personal_discount_plan_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("plans.id", ondelete="SET NULL"),
        index=True,
    )
    purchase_discount: Mapped[int]
    points: Mapped[int]

    is_blocked: Mapped[bool]
    is_bot_blocked: Mapped[bool]
    is_rules_accepted: Mapped[bool]
    is_trial_available: Mapped[bool]

    current_subscription_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(
            "subscriptions.id",
            ondelete="SET NULL",
        )
    )
