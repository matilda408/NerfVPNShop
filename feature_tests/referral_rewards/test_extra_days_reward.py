from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from remnapy.enums.users import TrafficLimitStrategy

from src.application.dto import PlanSnapshotDto, ReferralRewardDto, SubscriptionDto, UserDto
from src.application.events import ReferralRewardReceivedEvent
from src.application.use_cases.referral.commands.rewards import (
    GiveReferrerReward,
    GiveReferrerRewardDto,
)
from src.core.enums import PlanType, ReferralRewardType, SubscriptionStatus
from src.core.utils.time import datetime_now


pytestmark = pytest.mark.asyncio


@dataclass
class FakeUnitOfWork:
    commits: int = 0

    async def __aenter__(self) -> "FakeUnitOfWork":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ANN001
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


class FakeUserDao:
    def __init__(self, user: UserDto | None) -> None:
        self.user = user

    async def get_by_telegram_id(self, telegram_id: int) -> UserDto | None:
        if self.user and self.user.telegram_id == telegram_id:
            return self.user
        return None


class FakeSubscriptionDao:
    def __init__(self, current: SubscriptionDto | None) -> None:
        self.current = current
        self.created: SubscriptionDto | None = None
        self.created_for_telegram_id: int | None = None
        self.updated: SubscriptionDto | None = None

    async def get_current(self, telegram_id: int) -> SubscriptionDto | None:
        return self.current

    async def create(
        self,
        subscription: SubscriptionDto,
        telegram_id: int,
    ) -> SubscriptionDto:
        self.created = subscription
        self.created_for_telegram_id = telegram_id
        return subscription

    async def update(self, subscription: SubscriptionDto) -> SubscriptionDto:
        self.updated = subscription
        return subscription


class FakeReferralDao:
    def __init__(self) -> None:
        self.issued_reward_ids: list[int] = []

    async def mark_reward_as_issued(self, reward_id: int) -> None:
        self.issued_reward_ids.append(reward_id)


class FakeEventPublisher:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


class FakeChangeUserPoints:
    async def system(self, data: object) -> None:
        return None


class FakeRemnawave:
    def __init__(self) -> None:
        self.created_plans: list[PlanSnapshotDto] = []
        self.updated_subscriptions: list[SubscriptionDto] = []

    async def create_user(self, user: UserDto, plan: PlanSnapshotDto) -> SimpleNamespace:
        self.created_plans.append(plan)
        return SimpleNamespace(
            uuid=uuid4(),
            status=SubscriptionStatus.ACTIVE,
            expire_at=datetime_now() + timedelta(days=plan.duration),
            subscription_url=f"https://subs.example/{user.telegram_id}",
        )

    async def update_user(
        self,
        user: UserDto,
        uuid,  # noqa: ANN001
        subscription: SubscriptionDto,
    ) -> SimpleNamespace:
        self.updated_subscriptions.append(subscription)
        return SimpleNamespace(uuid=uuid, status=SubscriptionStatus.ACTIVE)


def make_user(telegram_id: int = 1143025712) -> UserDto:
    return UserDto(id=1, telegram_id=telegram_id, name="Amir")


def make_plan(duration: int = 5, is_trial: bool = True) -> PlanSnapshotDto:
    return PlanSnapshotDto(
        id=7,
        name="Trial",
        type=PlanType.BOTH,
        traffic_limit=100,
        device_limit=1,
        duration=duration,
        is_trial=is_trial,
        traffic_limit_strategy=TrafficLimitStrategy.NO_RESET,
    )


def make_reward(user: UserDto, amount: int = 5) -> ReferralRewardDto:
    return ReferralRewardDto(
        id=55,
        user_telegram_id=user.telegram_id,
        type=ReferralRewardType.EXTRA_DAYS,
        amount=amount,
        is_issued=False,
    )


def make_subscription(
    plan: PlanSnapshotDto,
    expire_at,
    is_trial: bool = True,
) -> SubscriptionDto:
    return SubscriptionDto(
        id=10,
        user_remna_id=uuid4(),
        status=SubscriptionStatus.ACTIVE,
        is_trial=is_trial,
        traffic_limit=plan.traffic_limit,
        device_limit=plan.device_limit,
        traffic_limit_strategy=plan.traffic_limit_strategy,
        expire_at=expire_at,
        url="https://subs.example/current",
        plan_snapshot=plan,
    )


def make_interactor(
    user: UserDto,
    subscription_dao: FakeSubscriptionDao,
    referral_dao: FakeReferralDao,
    event_publisher: FakeEventPublisher,
    remnawave: FakeRemnawave,
) -> GiveReferrerReward:
    return GiveReferrerReward(
        uow=FakeUnitOfWork(),
        user_dao=FakeUserDao(user),
        subscription_dao=subscription_dao,
        referral_dao=referral_dao,
        event_publisher=event_publisher,
        change_user_points=FakeChangeUserPoints(),
        remnawave=remnawave,
    )


async def test_extra_days_extend_trial_subscription_instead_of_creating_new_one() -> None:
    user = make_user()
    plan = make_plan(duration=5, is_trial=True)
    old_expire_at = datetime_now() + timedelta(days=5)
    subscription = make_subscription(plan=plan, expire_at=old_expire_at, is_trial=True)
    subscription_dao = FakeSubscriptionDao(current=subscription)
    referral_dao = FakeReferralDao()
    event_publisher = FakeEventPublisher()
    remnawave = FakeRemnawave()
    interactor = make_interactor(
        user=user,
        subscription_dao=subscription_dao,
        referral_dao=referral_dao,
        event_publisher=event_publisher,
        remnawave=remnawave,
    )

    await interactor.system(
        GiveReferrerRewardDto(
            user_telegram_id=user.telegram_id,
            reward=make_reward(user, amount=5),
            referred_name="yaroslav",
            plan_snapshot=plan,
        )
    )

    assert subscription_dao.created is None
    assert subscription_dao.updated is subscription
    assert remnawave.created_plans == []
    assert remnawave.updated_subscriptions == [subscription]
    assert subscription.is_trial is False
    assert subscription.status == SubscriptionStatus.ACTIVE
    assert subscription.expire_at >= old_expire_at + timedelta(days=5, seconds=-1)
    assert referral_dao.issued_reward_ids == [55]
    assert any(isinstance(event, ReferralRewardReceivedEvent) for event in event_publisher.events)


async def test_extra_days_create_bonus_subscription_when_user_has_no_subscription() -> None:
    user = make_user()
    plan = make_plan(duration=5, is_trial=True)
    subscription_dao = FakeSubscriptionDao(current=None)
    referral_dao = FakeReferralDao()
    event_publisher = FakeEventPublisher()
    remnawave = FakeRemnawave()
    interactor = make_interactor(
        user=user,
        subscription_dao=subscription_dao,
        referral_dao=referral_dao,
        event_publisher=event_publisher,
        remnawave=remnawave,
    )

    await interactor.system(
        GiveReferrerRewardDto(
            user_telegram_id=user.telegram_id,
            reward=make_reward(user, amount=5),
            referred_name="yaroslav",
            plan_snapshot=plan,
        )
    )

    assert remnawave.updated_subscriptions == []
    assert len(remnawave.created_plans) == 1
    assert remnawave.created_plans[0].duration == 5
    assert remnawave.created_plans[0].is_trial is False
    assert subscription_dao.created is not None
    assert subscription_dao.created_for_telegram_id == user.telegram_id
    assert subscription_dao.created.is_trial is False
    assert subscription_dao.created.plan_snapshot.duration == 5
    assert referral_dao.issued_reward_ids == [55]
    assert any(isinstance(event, ReferralRewardReceivedEvent) for event in event_publisher.events)
