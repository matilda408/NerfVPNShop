import re
from dataclasses import dataclass
from decimal import Decimal

from loguru import logger

from src.application.common import Interactor, Notifier, TranslatorRunner
from src.application.common.dao import PlanDao, SubscriptionDao, UserDao
from src.application.common.policy import Permission
from src.application.common.uow import UnitOfWork
from src.application.dto import MessagePayloadDto, PlanDto, UserDto
from src.application.services import PricingService
from src.core.enums import Currency
from src.core.types import AnyKeyboard

CUSTOM_EMOJI_PATTERN = re.compile(
    r'<tg-emoji\s+emoji-id=["\'](?P<id>\d+)["\'][^>]*>(?P<emoji>.*?)</tg-emoji>',
    re.DOTALL,
)

PERSONAL_DISCOUNT_KIND = "персональная скидка"
PURCHASE_DISCOUNT_KIND = "скидка на следующую покупку"


class DiscountNotificationMixin:
    plan_dao: PlanDao
    subscription_dao: SubscriptionDao
    notifier: Notifier
    pricing_service: PricingService
    i18n: TranslatorRunner

    async def _notify_discount(
        self,
        actor: UserDto,
        target_user: UserDto,
        *,
        plan_id: int | None,
        discount_kind: str,
    ) -> None:
        plan = await self._get_discount_plan(plan_id)
        if not plan:
            logger.warning(
                f"{actor.log} Discount notification skipped: "
                f"plan '{plan_id}' not found or has no RUB price"
            )
            return

        original_price = self._get_rub_price(plan)
        if original_price is None:
            logger.warning(
                f"{actor.log} Discount notification skipped: "
                f"RUB price not found for plan '{plan_id}'"
            )
            return

        current_subscription = await self.subscription_dao.get_current(target_user.telegram_id)
        reply_markup = self._get_payment_keyboard(
            is_trial=current_subscription.is_trial if current_subscription else True,
        )
        price_details = self.pricing_service.calculate(
            target_user,
            original_price,
            Currency.RUB,
            plan_id=plan.id,
        )

        try:
            await self.notifier.notify_user(
                user=target_user,
                payload=MessagePayloadDto(
                    i18n_key="ntf-user.discount-issued",
                    i18n_kwargs={
                        "discount_kind": discount_kind,
                        "plan_name": self._format_plan_name(self.i18n.get(plan.name)),
                        "original_amount": self._format_amount(
                            price_details.original_amount,
                        ),
                        "final_amount": self._format_amount(price_details.final_amount),
                        "currency": Currency.RUB.symbol,
                    },
                    reply_markup=reply_markup,
                    disable_default_markup=True,
                    delete_after=None,
                ),
            )
        except Exception as e:
            logger.warning(
                f"{actor.log} Failed to notify user '{target_user.telegram_id}' "
                f"about discount: {e}"
            )

    async def _get_discount_plan(self, plan_id: int | None) -> PlanDto | None:
        if plan_id is not None:
            return await self.plan_dao.get_by_id(plan_id)

        active_plans = await self.plan_dao.get_active_plans()
        return next(
            (
                plan
                for plan in active_plans
                if not plan.is_trial and self._get_rub_price(plan) is not None
            ),
            None,
        )

    def _get_rub_price(self, plan: PlanDto) -> Decimal | None:
        durations = sorted(plan.durations, key=lambda duration: duration.order_index)
        for duration in durations:
            for price in duration.prices:
                if price.currency == Currency.RUB:
                    return price.price
        return None

    def _format_plan_name(self, plan_name: str) -> str:
        name = CUSTOM_EMOJI_PATTERN.sub(r"\g<emoji>", plan_name)
        name = " ".join(name.split())
        return name.split("|", maxsplit=1)[0].strip()

    def _format_amount(self, amount: Decimal) -> str:
        if amount == amount.to_integral_value():
            return str(int(amount))
        return f"{amount.normalize():f}"

    def _get_payment_keyboard(self, is_trial: bool) -> AnyKeyboard:
        from src.telegram.keyboards import get_buy_keyboard, get_renew_keyboard  # noqa: PLC0415

        return get_buy_keyboard() if is_trial else get_renew_keyboard()


@dataclass(frozen=True)
class SetUserPersonalDiscountDto:
    telegram_id: int
    discount: int
    plan_id: int | None = None


class SetUserPersonalDiscount(
    DiscountNotificationMixin,
    Interactor[SetUserPersonalDiscountDto, None],
):
    required_permission = Permission.USER_EDITOR

    def __init__(
        self,
        uow: UnitOfWork,
        user_dao: UserDao,
        plan_dao: PlanDao,
        subscription_dao: SubscriptionDao,
        notifier: Notifier,
        pricing_service: PricingService,
        i18n: TranslatorRunner,
    ):
        self.uow = uow
        self.user_dao = user_dao
        self.plan_dao = plan_dao
        self.subscription_dao = subscription_dao
        self.notifier = notifier
        self.pricing_service = pricing_service
        self.i18n = i18n

    async def _execute(self, actor: UserDto, data: SetUserPersonalDiscountDto) -> None:
        if not (0 <= data.discount <= 100):
            raise ValueError(f"Invalid discount value '{data.discount}'")

        should_notify = False
        async with self.uow:
            target_user = await self.user_dao.get_by_telegram_id(data.telegram_id)
            if not target_user:
                raise ValueError(f"User '{data.telegram_id}' not found")

            target_user.personal_discount = data.discount
            target_user.personal_discount_plan_id = data.plan_id if data.discount > 0 else None
            should_notify = data.discount > 0
            await self.user_dao.update(target_user)
            await self.uow.commit()

        if should_notify:
            await self._notify_discount(
                actor,
                target_user,
                plan_id=data.plan_id,
                discount_kind=PERSONAL_DISCOUNT_KIND,
            )

        logger.info(
            f"{actor.log} Set personal discount to '{data.discount}' "
            f"for user '{data.telegram_id}' and plan '{data.plan_id}'"
        )


@dataclass(frozen=True)
class SetUserPurchaseDiscountDto:
    telegram_id: int
    discount: int
    plan_id: int | None = None


class SetUserPurchaseDiscount(
    DiscountNotificationMixin,
    Interactor[SetUserPurchaseDiscountDto, None],
):
    required_permission = Permission.USER_EDITOR

    def __init__(
        self,
        uow: UnitOfWork,
        user_dao: UserDao,
        plan_dao: PlanDao,
        subscription_dao: SubscriptionDao,
        notifier: Notifier,
        pricing_service: PricingService,
        i18n: TranslatorRunner,
    ):
        self.uow = uow
        self.user_dao = user_dao
        self.plan_dao = plan_dao
        self.subscription_dao = subscription_dao
        self.notifier = notifier
        self.pricing_service = pricing_service
        self.i18n = i18n

    async def _execute(self, actor: UserDto, data: SetUserPurchaseDiscountDto) -> None:
        if not (0 <= data.discount <= 100):
            raise ValueError(f"Invalid discount value '{data.discount}'")

        should_notify = False
        async with self.uow:
            target_user = await self.user_dao.get_by_telegram_id(data.telegram_id)
            if not target_user:
                raise ValueError(f"User '{data.telegram_id}' not found")

            target_user.purchase_discount = data.discount
            target_user.purchase_discount_plan_id = data.plan_id if data.discount > 0 else None
            should_notify = data.discount > 0
            await self.user_dao.update(target_user)
            await self.uow.commit()

        if should_notify:
            await self._notify_discount(
                actor,
                target_user,
                plan_id=data.plan_id,
                discount_kind=PURCHASE_DISCOUNT_KIND,
            )

        logger.info(
            f"{actor.log} Set purchase discount to '{data.discount}' "
            f"for user '{data.telegram_id}' and plan '{data.plan_id}'"
        )


class ToggleUserTrialAvailable(Interactor[int, None]):
    required_permission = Permission.USER_EDITOR

    def __init__(self, uow: UnitOfWork, user_dao: UserDao):
        self.uow = uow
        self.user_dao = user_dao

    async def _execute(self, actor: UserDto, data: int) -> None:
        async with self.uow:
            target_user = await self.user_dao.get_by_telegram_id(data)
            if not target_user:
                raise ValueError(f"User '{data}' not found")

            new_value = not target_user.is_trial_available
            await self.user_dao.set_trial_available(data, new_value)
            await self.uow.commit()

        logger.info(f"{actor.log} Set trial available to '{new_value}' for user '{data}'")


@dataclass(frozen=True)
class ChangeUserPointsDto:
    telegram_id: int
    amount: int


class ChangeUserPoints(Interactor[ChangeUserPointsDto, None]):
    required_permission = Permission.USER_EDITOR

    def __init__(self, uow: UnitOfWork, user_dao: UserDao):
        self.uow = uow
        self.user_dao = user_dao

    async def _execute(self, actor: UserDto, data: ChangeUserPointsDto) -> None:
        async with self.uow:
            target_user = await self.user_dao.get_by_telegram_id(data.telegram_id)
            if not target_user:
                logger.error(f"{actor.log} User not found with id '{data.telegram_id}'")
                raise ValueError(f"User '{data.telegram_id}' not found")

            new_points = target_user.points + data.amount
            if new_points < 0:
                raise ValueError(
                    f"{actor.log} Points balance cannot be negative for '{data.telegram_id}'"
                )

            target_user.points = new_points
            await self.user_dao.update(target_user)
            await self.uow.commit()

        operation = "Added" if data.amount > 0 else "Subtracted"
        logger.info(f"{actor.log} {operation} '{abs(data.amount)}' points for '{data.telegram_id}'")
