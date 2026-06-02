import asyncio
import re
import time
from datetime import datetime, timedelta
from decimal import Decimal

from adaptix import Retort
from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from redis.asyncio import Redis

from src.application.common import EventPublisher, Notifier, Remnawave, TranslatorRunner
from src.application.common.dao import PlanDao, SubscriptionDao, UserDao
from src.application.common.uow import UnitOfWork
from src.application.dto import MessagePayloadDto, PlanDto, UserDto
from src.application.events import SubscriptionExpiresEvent, TrialNotConnectedEvent
from src.application.services import PricingService
from src.core.constants import BATCH_DELAY, BATCH_SIZE_20, TTL_1D, TTL_7D
from src.core.enums import Currency, UserNotificationType
from src.core.utils.iterables import chunked
from src.core.utils.time import datetime_now
from src.infrastructure.redis.keys import (
    PurchaseDiscountMonthlyCorrectionKey,
    SubscriptionExpiryReminderKey,
    TrialExpiredDiscountReminderKey,
    TrialExpiredWithoutPurchaseDiscountKey,
    TrialNotConnectedDiscountBackfillKey,
    TrialNotConnectedDiscountKey,
    TrialNotConnectedReminderKey,
)
from src.infrastructure.taskiq.broker import broker
from src.telegram.keyboards import get_buy_keyboard

CUSTOM_EMOJI_PATTERN = re.compile(
    r'<tg-emoji\s+emoji-id=["\'](?P<id>\d+)["\'][^>]*>(?P<emoji>.*?)</tg-emoji>',
    re.DOTALL,
)


def _format_plan_name(plan_name: str) -> str:
    name = CUSTOM_EMOJI_PATTERN.sub(r"\g<emoji>", plan_name)
    name = " ".join(name.split())
    return name.split("|", maxsplit=1)[0].strip()


def _format_amount(amount: Decimal) -> str:
    if amount == amount.to_integral_value():
        return str(int(amount))
    return f"{amount.normalize():f}"


def _get_rub_price(plan: PlanDto) -> Decimal | None:
    durations = sorted(plan.durations, key=lambda duration: duration.order_index)
    for duration in durations:
        for price in duration.prices:
            if price.currency == Currency.RUB:
                return price.price
    return None


async def _get_discount_plan(
    user: UserDto,
    plan_dao: PlanDao,
) -> PlanDto | None:
    if user.personal_discount_plan_id is not None:
        return await plan_dao.get_by_id(user.personal_discount_plan_id)

    active_plans = await plan_dao.get_active_plans()
    return next((plan for plan in active_plans if not plan.is_trial), None)


def _has_monthly_duration(plan: PlanDto) -> bool:
    return any(duration.days in {30, 31} for duration in plan.durations)


def _is_monthly_plan_name(plan_name: str) -> bool:
    name = _format_plan_name(plan_name).lower()
    return "месяч" in name and not any(marker in name for marker in ("3", "6", "тр"))


async def _get_purchase_discount_plan(
    plan_dao: PlanDao,
    i18n: TranslatorRunner,
) -> PlanDto | None:
    active_plans = await plan_dao.get_active_plans()
    eligible_plans = [
        plan
        for plan in active_plans
        if not plan.is_trial and _get_rub_price(plan) is not None
    ]

    monthly_plan = next((plan for plan in eligible_plans if _has_monthly_duration(plan)), None)
    if monthly_plan:
        return monthly_plan

    return next(
        (plan for plan in eligible_plans if _is_monthly_plan_name(i18n.get(plan.name))),
        None,
    )


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


@broker.task(schedule=[{"cron": "* * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def correct_purchase_discounts_to_monthly_plan_task(
    user_dao: FromDishka[UserDao],
    plan_dao: FromDishka[PlanDao],
    uow: FromDishka[UnitOfWork],
    redis: FromDishka[Redis],
    retort: FromDishka[Retort],
    i18n: FromDishka[TranslatorRunner],
) -> None:
    correction_key = retort.dump(PurchaseDiscountMonthlyCorrectionKey())
    is_correction_finished = await redis.get(correction_key)
    if is_correction_finished:
        logger.debug("Purchase discount monthly correction already finished")
        return

    plan = await _get_purchase_discount_plan(plan_dao, i18n)
    if not plan or plan.id is None:
        logger.warning("Monthly plan not found for purchase discount correction")
        return

    users = await user_dao.get_purchase_discounts_without_plan()
    if not users:
        await redis.set(correction_key, "1")
        logger.debug("No purchase discounts without plan found")
        return

    async with uow:
        for user in users:
            user.purchase_discount_plan_id = plan.id
            await user_dao.update(user)
        await uow.commit()

    await redis.set(correction_key, "1")
    logger.info(f"Corrected '{len(users)}' purchase discounts to monthly plan '{plan.id}'")


@broker.task(schedule=[{"cron": "* * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def grant_discount_for_trial_not_connected_task(  # noqa: C901
    subscription_dao: FromDishka[SubscriptionDao],
    user_dao: FromDishka[UserDao],
    plan_dao: FromDishka[PlanDao],
    uow: FromDishka[UnitOfWork],
    redis: FromDishka[Redis],
    retort: FromDishka[Retort],
    remnawave: FromDishka[Remnawave],
    notifier: FromDishka[Notifier],
    pricing_service: FromDishka[PricingService],
    i18n: FromDishka[TranslatorRunner],
) -> None:
    now = datetime_now()
    start_at = now - timedelta(days=3, minutes=5)
    end_at = now - timedelta(days=3)

    trial_subscriptions = await subscription_dao.get_current_trials_created_between_any_status(
        start_at,
        end_at,
    )
    if not trial_subscriptions:
        logger.debug("No three-day trial no-connection discounts pending")
        return

    plan = await _get_purchase_discount_plan(plan_dao, i18n)
    if not plan:
        logger.warning("No active non-trial plan with RUB price found for no-connection discount")
        return

    original_price = _get_rub_price(plan)
    if original_price is None:
        logger.warning(f"No RUB price found for plan '{plan.id}'")
        return

    granted_count = 0

    for user, subscription in trial_subscriptions:
        if subscription.id is None or subscription.created_at is None:
            logger.warning(
                f"Skipping three-day trial no-connection discount for subscription without "
                f"ID or created_at: '{subscription}'"
            )
            continue

        if user.purchase_discount >= 40 and user.purchase_discount_plan_id == plan.id:
            logger.debug(
                f"Skipping three-day trial no-connection discount for user "
                f"'{user.telegram_id}': purchase discount is already '{user.purchase_discount}'"
            )
            continue

        try:
            remna_user = await remnawave.get_user_by_uuid(subscription.user_remna_id)
        except Exception as e:
            logger.exception(
                f"Failed to fetch RemnaUser '{subscription.user_remna_id}' "
                f"for three-day trial no-connection discount: {e}"
            )
            continue

        if not remna_user:
            logger.debug(
                f"Skipping three-day trial no-connection discount: RemnaUser "
                f"'{subscription.user_remna_id}' not found"
            )
            continue

        first_connected_at = getattr(remna_user, "first_connected_at", None)
        last_connected_at = getattr(remna_user, "last_connected_at", None)
        if first_connected_at or last_connected_at:
            continue

        if user.purchase_discount >= 40:
            user.purchase_discount_plan_id = plan.id
            async with uow:
                await user_dao.update(user)
                await uow.commit()
            continue

        discount_key = retort.dump(
            TrialNotConnectedDiscountKey(
                subscription_id=subscription.id,
                created_at=int(subscription.created_at.timestamp()),
            )
        )
        is_first_discount = await redis.set(discount_key, "1", ex=TTL_7D, nx=True)
        if not is_first_discount:
            continue

        user.purchase_discount = 40
        user.purchase_discount_plan_id = plan.id

        async with uow:
            await user_dao.update(user)
            await uow.commit()

        pricing = pricing_service.calculate(user, original_price, Currency.RUB, plan_id=plan.id)
        await notifier.notify_user(
            user=user,
            payload=MessagePayloadDto(
                i18n_key="ntf-user.discount-issued",
                i18n_kwargs={
                    "discount_kind": "скидка на следующую покупку",
                    "plan_name": _format_plan_name(i18n.get(plan.name)),
                    "original_amount": _format_amount(pricing.original_amount),
                    "final_amount": _format_amount(pricing.final_amount),
                    "currency": Currency.RUB.symbol,
                },
                reply_markup=get_buy_keyboard(),
                disable_default_markup=True,
                delete_after=None,
            ),
        )
        granted_count += 1

    logger.info(f"Granted '{granted_count}' three-day trial no-connection discounts")


@broker.task(schedule=[{"cron": "* * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def backfill_discount_for_old_trial_not_connected_task(  # noqa: C901
    subscription_dao: FromDishka[SubscriptionDao],
    user_dao: FromDishka[UserDao],
    plan_dao: FromDishka[PlanDao],
    uow: FromDishka[UnitOfWork],
    redis: FromDishka[Redis],
    retort: FromDishka[Retort],
    remnawave: FromDishka[Remnawave],
    notifier: FromDishka[Notifier],
    pricing_service: FromDishka[PricingService],
    i18n: FromDishka[TranslatorRunner],
) -> None:
    backfill_key = retort.dump(TrialNotConnectedDiscountBackfillKey())
    is_backfill_finished = await redis.get(backfill_key)
    if is_backfill_finished:
        logger.debug("Old trial no-connection discount backfill already finished")
        return

    cutoff = datetime_now() - timedelta(days=3)
    trial_subscriptions = await subscription_dao.get_old_trials_without_paid_subscription(cutoff)

    plan = await _get_purchase_discount_plan(plan_dao, i18n)
    if not plan:
        logger.warning("No active non-trial plan with RUB price found for old trial backfill")
        return

    original_price = _get_rub_price(plan)
    if original_price is None:
        logger.warning(f"No RUB price found for plan '{plan.id}'")
        return

    granted_count = 0
    had_lookup_errors = False
    processed_user_ids: set[int] = set()

    for user, subscription in trial_subscriptions:
        if user.telegram_id in processed_user_ids:
            continue

        if subscription.id is None or subscription.created_at is None:
            logger.warning(
                f"Skipping old trial no-connection discount for subscription without "
                f"ID or created_at: '{subscription}'"
            )
            continue

        if user.purchase_discount >= 40 and user.purchase_discount_plan_id == plan.id:
            logger.debug(
                f"Skipping old trial no-connection discount for user "
                f"'{user.telegram_id}': purchase discount is already '{user.purchase_discount}'"
            )
            processed_user_ids.add(user.telegram_id)
            continue

        try:
            remna_user = await remnawave.get_user_by_uuid(subscription.user_remna_id)
        except Exception as e:
            had_lookup_errors = True
            logger.exception(
                f"Failed to fetch RemnaUser '{subscription.user_remna_id}' "
                f"for old trial no-connection discount: {e}"
            )
            continue

        if not remna_user:
            logger.debug(
                f"Skipping old trial no-connection discount: RemnaUser "
                f"'{subscription.user_remna_id}' not found"
            )
            continue

        first_connected_at = getattr(remna_user, "first_connected_at", None)
        last_connected_at = getattr(remna_user, "last_connected_at", None)
        if first_connected_at or last_connected_at:
            processed_user_ids.add(user.telegram_id)
            continue

        if user.purchase_discount >= 40:
            user.purchase_discount_plan_id = plan.id
            async with uow:
                await user_dao.update(user)
                await uow.commit()
            processed_user_ids.add(user.telegram_id)
            continue

        discount_key = retort.dump(
            TrialNotConnectedDiscountKey(
                subscription_id=subscription.id,
                created_at=int(subscription.created_at.timestamp()),
            )
        )
        is_first_discount = await redis.set(discount_key, "1", ex=TTL_7D, nx=True)
        if not is_first_discount:
            processed_user_ids.add(user.telegram_id)
            continue

        user.purchase_discount = 40
        user.purchase_discount_plan_id = plan.id

        async with uow:
            await user_dao.update(user)
            await uow.commit()

        pricing = pricing_service.calculate(user, original_price, Currency.RUB, plan_id=plan.id)
        await notifier.notify_user(
            user=user,
            payload=MessagePayloadDto(
                i18n_key="ntf-user.discount-issued",
                i18n_kwargs={
                    "discount_kind": "скидка на следующую покупку",
                    "plan_name": _format_plan_name(i18n.get(plan.name)),
                    "original_amount": _format_amount(pricing.original_amount),
                    "final_amount": _format_amount(pricing.final_amount),
                    "currency": Currency.RUB.symbol,
                },
                reply_markup=get_buy_keyboard(),
                disable_default_markup=True,
                delete_after=None,
            ),
        )
        processed_user_ids.add(user.telegram_id)
        granted_count += 1

    if had_lookup_errors:
        logger.warning(
            f"Backfilled '{granted_count}' old trial no-connection discounts, "
            "but Remnawave lookup errors occurred; backfill will retry later"
        )
        return

    await redis.set(backfill_key, "1")
    logger.info(f"Backfilled '{granted_count}' old trial no-connection discounts")


@broker.task(schedule=[{"cron": "* * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def grant_discount_for_expired_trial_without_purchase_task(
    subscription_dao: FromDishka[SubscriptionDao],
    user_dao: FromDishka[UserDao],
    plan_dao: FromDishka[PlanDao],
    uow: FromDishka[UnitOfWork],
    redis: FromDishka[Redis],
    retort: FromDishka[Retort],
    notifier: FromDishka[Notifier],
    pricing_service: FromDishka[PricingService],
    i18n: FromDishka[TranslatorRunner],
) -> None:
    now = datetime_now()
    start_at = now - timedelta(days=3, minutes=5)
    end_at = now - timedelta(days=3)

    expired_trials = await subscription_dao.get_current_trials_expired_between_any_discount(
        start_at,
        end_at,
    )
    if not expired_trials:
        logger.debug("No three-day expired trial no-purchase discounts pending")
        return

    plan = await _get_purchase_discount_plan(plan_dao, i18n)
    if not plan:
        logger.warning("No active non-trial plan with RUB price found for no-purchase discount")
        return

    original_price = _get_rub_price(plan)
    if original_price is None:
        logger.warning(f"No RUB price found for plan '{plan.id}'")
        return

    granted_count = 0

    for user, subscription in expired_trials:
        if subscription.id is None:
            logger.warning(
                f"Skipping three-day expired trial no-purchase discount for subscription without "
                f"ID: '{subscription}'"
            )
            continue

        if user.purchase_discount >= 40 and user.purchase_discount_plan_id == plan.id:
            logger.debug(
                f"Skipping three-day expired trial no-purchase discount for user "
                f"'{user.telegram_id}': purchase discount is already '{user.purchase_discount}'"
            )
            continue

        if user.purchase_discount >= 40:
            user.purchase_discount_plan_id = plan.id
            async with uow:
                await user_dao.update(user)
                await uow.commit()
            continue

        discount_key = retort.dump(
            TrialExpiredWithoutPurchaseDiscountKey(
                subscription_id=subscription.id,
                expire_at=int(subscription.expire_at.timestamp()),
            )
        )
        is_first_discount = await redis.set(discount_key, "1", ex=TTL_7D, nx=True)
        if not is_first_discount:
            continue

        user.purchase_discount = 40
        user.purchase_discount_plan_id = plan.id

        async with uow:
            await user_dao.update(user)
            await uow.commit()

        pricing = pricing_service.calculate(user, original_price, Currency.RUB, plan_id=plan.id)
        await notifier.notify_user(
            user=user,
            payload=MessagePayloadDto(
                i18n_key="ntf-user.discount-issued",
                i18n_kwargs={
                    "discount_kind": "скидка на следующую покупку",
                    "plan_name": _format_plan_name(i18n.get(plan.name)),
                    "original_amount": _format_amount(pricing.original_amount),
                    "final_amount": _format_amount(pricing.final_amount),
                    "currency": Currency.RUB.symbol,
                },
                reply_markup=get_buy_keyboard(),
                disable_default_markup=True,
                delete_after=None,
            ),
        )
        granted_count += 1

    logger.info(f"Granted '{granted_count}' three-day expired trial no-purchase discounts")


@broker.task(schedule=[{"cron": "* * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def notify_expired_trial_discount_task(
    subscription_dao: FromDishka[SubscriptionDao],
    plan_dao: FromDishka[PlanDao],
    redis: FromDishka[Redis],
    retort: FromDishka[Retort],
    notifier: FromDishka[Notifier],
    i18n: FromDishka[TranslatorRunner],
) -> None:
    now = datetime_now()
    start_at = now - timedelta(minutes=20)
    end_at = now - timedelta(minutes=15)

    expired_trials = await subscription_dao.get_current_trials_expired_between(start_at, end_at)
    if not expired_trials:
        logger.debug("No expired trial discount reminders pending")
        return

    notified_count = 0

    for user, subscription in expired_trials:
        if subscription.id is None:
            logger.warning(
                f"Skipping expired trial discount reminder for subscription without ID: "
                f"'{subscription}'"
            )
            continue

        plan = await _get_discount_plan(user, plan_dao)
        if not plan:
            logger.warning(
                f"Skipping expired trial discount reminder for user '{user.telegram_id}': "
                "discount plan not found"
            )
            continue

        reminder_key = retort.dump(
            TrialExpiredDiscountReminderKey(
                subscription_id=subscription.id,
                expire_at=int(subscription.expire_at.timestamp()),
            )
        )
        is_first_notification = await redis.set(reminder_key, "1", ex=TTL_7D, nx=True)
        if not is_first_notification:
            continue

        await notifier.notify_user(
            user=user,
            payload=MessagePayloadDto(
                i18n_key="ntf-user.expired-trial-discount",
                i18n_kwargs={"plan_name": _format_plan_name(i18n.get(plan.name))},
                reply_markup=get_buy_keyboard(),
                disable_default_markup=True,
                delete_after=None,
            ),
        )
        notified_count += 1

    logger.info(f"Sent '{notified_count}' expired trial discount reminders")


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
