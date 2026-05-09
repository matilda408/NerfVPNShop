import asyncio
import time
from datetime import datetime, timedelta

from adaptix import Retort
from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from redis.asyncio import Redis

from src.application.common import EventPublisher, Notifier, Remnawave
from src.application.common.dao import SubscriptionDao, UserDao
from src.application.common.uow import UnitOfWork
from src.application.dto import MessagePayloadDto
from src.application.events import SubscriptionExpiresEvent, TrialNotConnectedEvent
from src.core.constants import BATCH_DELAY, BATCH_SIZE_20, TTL_1D, TTL_7D
from src.core.enums import UserNotificationType
from src.core.utils.iterables import chunked
from src.core.utils.time import datetime_now
from src.infrastructure.redis.keys import (
    SubscriptionExpiryReminderKey,
    TrialNotConnectedReminderKey,
)
from src.infrastructure.taskiq.broker import broker


async def _notify_subscriptions_expiring(
    *,
    subscription_dao: SubscriptionDao,
    redis: Redis,
    retort: Retort,
    event_publisher: EventPublisher,
    start_at: datetime,
    end_at: datetime,
    day: int,
    hour: int,
    notification_type: UserNotificationType,
    log_label: str,
) -> None:
    expiring_subscriptions = await subscription_dao.get_current_expiring_between(start_at, end_at)
    if not expiring_subscriptions:
        logger.debug(f"No subscriptions expiring in {log_label}")
        return

    notified_count = 0

    for user, subscription in expiring_subscriptions:
        if subscription.id is None:
            logger.warning(
                f"Skipping {log_label} reminder for subscription without ID: '{subscription}'"
            )
            continue

        reminder_key = retort.dump(
            SubscriptionExpiryReminderKey(
                subscription_id=subscription.id,
                expire_at=int(subscription.expire_at.timestamp()),
                notification_type=notification_type,
            )
        )
        is_first_notification = await redis.set(reminder_key, "1", ex=TTL_1D, nx=True)
        if not is_first_notification:
            continue

        await event_publisher.publish(
            SubscriptionExpiresEvent(
                user=user,
                is_trial=subscription.is_trial,
                day=day,
                hour=hour,
                notification_type=notification_type,
            )
        )
        notified_count += 1

    logger.info(f"Sent '{notified_count}' {log_label} expiration notifications")


@broker.task(schedule=[{"cron": "* * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def notify_subscriptions_expiring_in_one_day_task(
    subscription_dao: FromDishka[SubscriptionDao],
    redis: FromDishka[Redis],
    retort: FromDishka[Retort],
    event_publisher: FromDishka[EventPublisher],
) -> None:
    now = datetime_now()
    start_at = now + timedelta(hours=23, minutes=55)
    end_at = now + timedelta(days=1)

    await _notify_subscriptions_expiring(
        subscription_dao=subscription_dao,
        redis=redis,
        retort=retort,
        event_publisher=event_publisher,
        start_at=start_at,
        end_at=end_at,
        day=1,
        hour=0,
        notification_type=UserNotificationType.EXPIRES_IN_1_DAY,
        log_label="one-day",
    )


@broker.task(schedule=[{"cron": "* * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def notify_subscriptions_expiring_in_one_hour_task(
    subscription_dao: FromDishka[SubscriptionDao],
    redis: FromDishka[Redis],
    retort: FromDishka[Retort],
    event_publisher: FromDishka[EventPublisher],
) -> None:
    now = datetime_now()
    start_at = now + timedelta(minutes=55)
    end_at = now + timedelta(hours=1)

    await _notify_subscriptions_expiring(
        subscription_dao=subscription_dao,
        redis=redis,
        retort=retort,
        event_publisher=event_publisher,
        start_at=start_at,
        end_at=end_at,
        day=0,
        hour=1,
        notification_type=UserNotificationType.EXPIRES_IN_1_HOUR,
        log_label="one-hour",
    )


@broker.task(schedule=[{"cron": "* * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def notify_trial_subscriptions_not_connected_task(
    subscription_dao: FromDishka[SubscriptionDao],
    redis: FromDishka[Redis],
    retort: FromDishka[Retort],
    remnawave: FromDishka[Remnawave],
    event_publisher: FromDishka[EventPublisher],
) -> None:
    now = datetime_now()
    start_at = now - timedelta(hours=2)
    end_at = now - timedelta(hours=1)

    trial_subscriptions = await subscription_dao.get_current_trials_created_between(
        start_at,
        end_at,
    )
    if not trial_subscriptions:
        logger.debug("No trial subscriptions pending first-connection reminder")
        return

    notified_count = 0

    for user, subscription in trial_subscriptions:
        if subscription.id is None or subscription.created_at is None:
            logger.warning(
                f"Skipping trial first-connection reminder for subscription without "
                f"ID or created_at: '{subscription}'"
            )
            continue

        try:
            remna_user = await remnawave.get_user_by_uuid(subscription.user_remna_id)
        except Exception as e:
            logger.exception(
                f"Failed to fetch RemnaUser '{subscription.user_remna_id}' "
                f"for trial first-connection reminder: {e}"
            )
            continue

        if not remna_user:
            logger.debug(
                f"Skipping trial first-connection reminder: RemnaUser "
                f"'{subscription.user_remna_id}' not found"
            )
            continue

        first_connected_at = getattr(remna_user, "first_connected_at", None)
        last_connected_at = getattr(remna_user, "last_connected_at", None)
        if first_connected_at or last_connected_at:
            continue

        reminder_key = retort.dump(
            TrialNotConnectedReminderKey(
                subscription_id=subscription.id,
                created_at=int(subscription.created_at.timestamp()),
            )
        )
        is_first_notification = await redis.set(reminder_key, "1", ex=TTL_7D, nx=True)
        if not is_first_notification:
            continue

        await event_publisher.publish(
            TrialNotConnectedEvent(
                user=user,
                subscription_url=subscription.url,
                device_count=subscription.device_limit,
            )
        )
        notified_count += 1

    logger.info(f"Sent '{notified_count}' trial first-connection reminders")


@broker.task
@inject(patch_module=True)
async def notify_payments_restored(
    waiting_user_ids: list[int],
    uow: FromDishka[UnitOfWork],
    user_dao: FromDishka[UserDao],
    notifier: FromDishka[Notifier],
) -> None:
    users = await user_dao.get_by_telegram_ids(waiting_user_ids)

    if not users:
        logger.debug("No users found for access notification")
        return

    total_users = len(users)
    total_errors = 0
    start_time = time.perf_counter()

    logger.info(f"Starting access broadcast for '{total_users}' users")

    for i, batch in enumerate(chunked(users, BATCH_SIZE_20), start=1):
        batch_start = time.perf_counter()

        tasks = [
            notifier.notify_user(
                user=user,
                payload=MessagePayloadDto(
                    i18n_key="ntf-access.payments-restored",
                    disable_default_markup=False,
                    delete_after=None,
                ),
            )
            for user in batch
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        batch_errors = sum(1 for result in results if isinstance(result, Exception))
        total_errors += batch_errors

        batch_elapsed = time.perf_counter() - batch_start

        logger.info(
            f"Batch '{i}' processed: sent '{len(batch) - batch_errors}' success, "
            f"'{batch_errors}' errors in '{batch_elapsed:.2f}'s"
        )

        wait_time = BATCH_DELAY - batch_elapsed
        if wait_time > 0:
            await asyncio.sleep(wait_time)

    total_duration = time.perf_counter() - start_time

    logger.info(
        f"Access broadcast for '{total_users}' users completed in '{total_duration:.2f}'s: "
        f"'{total_users - total_errors}' success, '{total_errors}' errors"
    )
