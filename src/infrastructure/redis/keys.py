from dataclasses import dataclass
from typing import Union

from src.core.enums import Role, UserNotificationType

from .key_builder import StorageKey

SETTINGS_PREFIX = "settings"

USER_LIST_PREFIX = "user_list"
USER_COUNT_PREFIX = "user_count"

# TODO: Add version field?


@dataclass(frozen=True)
class UserCacheKey(StorageKey, prefix="user"):
    telegram_id: int


@dataclass(frozen=True)
class RoleKey(StorageKey, prefix="user_list"):
    role: Union[Role, tuple[Role, ...]]


@dataclass(frozen=True)
class WebhookLockKey(StorageKey, prefix="webhook_lock"):
    bot_id: int
    webhook_hash: str


@dataclass(frozen=True)
class LatestNotifiedVersionKey(StorageKey, prefix="latest_notified_version"):
    version: str


@dataclass(frozen=True)
class SubscriptionExpiryReminderKey(StorageKey, prefix="subscription_expiry_reminder"):
    subscription_id: int
    expire_at: int
    notification_type: UserNotificationType


@dataclass(frozen=True)
class TrialNotConnectedReminderKey(StorageKey, prefix="trial_not_connected_reminder"):
    subscription_id: int
    created_at: int


@dataclass(frozen=True)
class TrialNotConnectedDiscountKey(StorageKey, prefix="trial_not_connected_discount"):
    subscription_id: int
    created_at: int


class TrialNotConnectedDiscountBackfillKey(
    StorageKey,
    prefix="trial_not_connected_discount_backfill_monthly_v2",
): ...


@dataclass(frozen=True)
class TrialExpiredDiscountReminderKey(StorageKey, prefix="trial_expired_discount_reminder"):
    subscription_id: int
    expire_at: int


@dataclass(frozen=True)
class TrialExpiredWithoutPurchaseDiscountKey(
    StorageKey,
    prefix="trial_expired_without_purchase_discount",
):
    subscription_id: int
    expire_at: int


class PurchaseDiscountMonthlyCorrectionKey(
    StorageKey,
    prefix="purchase_discount_monthly_correction_v1",
): ...


class PaymentWaitlistKey(StorageKey, prefix="payment_waitlist"): ...


class ImportRunningKey(StorageKey, prefix="import_running"): ...


class SyncRunningKey(StorageKey, prefix="sync_running"): ...
