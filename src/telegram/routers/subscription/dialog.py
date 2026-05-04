from aiogram.enums import ButtonStyle
from aiogram_dialog import Dialog, StartMode, Window
from aiogram_dialog.widgets.kbd import Button, Column, Group, Row, Select, Start, SwitchTo, Url
from aiogram_dialog.widgets.style import Style
from aiogram_dialog.widgets.text import Format
from magic_filter import F

from src.core.constants import PAYMENT_PREFIX
from src.core.enums import BannerName, PurchaseType
from src.telegram.keyboards import back_main_menu_button
from src.telegram.states import MainMenu, Subscription
from src.telegram.widgets import Banner, I18nFormat, IgnoreUpdate
from src.telegram.widgets.icon_buttons import IconButton, IconSelect, IconSwitchTo
from src.telegram.widgets.icon_start import IconStart

from .getters import (
    confirm_getter,
    duration_getter,
    getter_connect,
    payment_method_getter,
    plan_getter,
    plans_getter,
    subscription_getter,
    success_payment_getter,
)
from .handlers import (
    on_duration_select,
    on_get_trial,
    on_get_subscription,
    on_payment_method_select,
    on_plan_select,
    on_subscription_plans,
    on_subscription_start,
)

subscription = Window(
    Banner(BannerName.SUBSCRIPTION),
    I18nFormat("msg-subscription-main"),
    Row(
        IconButton(
            text=I18nFormat("btn-subscription.new"),
            id=f"{PAYMENT_PREFIX}{PurchaseType.NEW}",
            on_click=on_subscription_plans,
            when=~F["has_active_subscription"] & ~F["trial_available"],
            icon_custom_emoji_id="5258204546391351475",
        ),
        Button(
            text=I18nFormat("btn-menu.trial"),
            id="trial",
            on_click=on_get_trial,
            when=F["trial_available"],
            style=Style(ButtonStyle.SUCCESS),
        ),
        IconButton(
            text=I18nFormat("btn-subscription.renew"),
            id=f"{PAYMENT_PREFIX}{PurchaseType.CHANGE}",
            on_click=on_subscription_plans,
            when=F["has_active_subscription"] & F["is_not_unlimited"],
            icon_custom_emoji_id="5258108352008823107",
        ),
        # Button(
        #     text=I18nFormat("btn-subscription.change"),
        #     id=f"{PAYMENT_PREFIX}{PurchaseType.CHANGE}",
        #     on_click=on_subscription_plans,
        #     when=F["has_active_subscription"],
        # ),
    ),
    # Row(
    #     Button(
    #         text=I18nFormat("btn-subscription.promocode"),
    #         id=f"{PAYMENT_PREFIX}promocode",
    #         on_click=show_dev_popup,
    #         # state=Subscription.PROMOCODE,
    #     ),
    # ),
    Row(
        IconStart(
            text=I18nFormat("btn-menu.instruction"),
            id=f"{PAYMENT_PREFIX}instruction",
            state=MainMenu.INSTRUCTION,
            mode=StartMode.RESET_STACK,
            when=F["connectable"],
            icon_custom_emoji_id="5258328383183396223",
        ),
    ),
    Row(
        IconStart(
            text=I18nFormat("btn-devices.delete-device"),
            id=f"{PAYMENT_PREFIX}delete_device",
            state=MainMenu.DEVICES,
            mode=StartMode.RESET_STACK,
            when=F["connectable"],
            icon_custom_emoji_id="5258130763148172425",
        ),
    ),
    # Row(
    #     IconStart(
    #         text=I18nFormat("btn-devices.reissue"),
    #         id=f"{PAYMENT_PREFIX}reissue_subscription",
    #         state=MainMenu.DEVICE_CONFIRM_REISSUE,
    #         mode=StartMode.RESET_STACK,
    #         when=F["connectable"],
    #         icon_custom_emoji_id="6030657343744644592",
    #     ),
    # ),
    Row(
        IconStart(
            text=I18nFormat("btn-back.general"),
            id=f"{PAYMENT_PREFIX}back",
            state=MainMenu.MAIN,
            mode=StartMode.RESET_STACK,
            icon_custom_emoji_id="5258236805890710909",
        ),
    ),
    IgnoreUpdate(),
    state=Subscription.MAIN,
    getter=subscription_getter,
)

plan = Window(
    Banner(BannerName.TARIFF),
    I18nFormat("msg-subscription-plan"),
    Column(
        Select(
            text=I18nFormat("btn-subscription.plan"),
            id=f"{PAYMENT_PREFIX}select_plan",
            item_id_getter=lambda item: item,
            items="plan_id",
            type_factory=int,
            on_click=on_plan_select,
        ),
    ),
    *back_main_menu_button,
    IgnoreUpdate(),
    state=Subscription.PLAN,
    getter=plan_getter,
)


plans = Window(
    Banner(BannerName.TARIFF),
    I18nFormat("msg-subscription-plans"),
    Column(
        IconSelect(
            text=Format("{item[name]}"),
            id=f"{PAYMENT_PREFIX}select_plan",
            item_id_getter=lambda item: item["id"],
            items="plans",
            type_factory=int,
            on_click=on_plan_select,
            icon_items_key="plans",
            icon_custom_emoji_id_getter=lambda item: item.get("icon_custom_emoji_id"),
        ),
    ),
    Row(
        IconStart(
            text=I18nFormat("btn-back.general"),
            id=f"{PAYMENT_PREFIX}back",
            state=MainMenu.MAIN,
            mode=StartMode.RESET_STACK,
            icon_custom_emoji_id="5258236805890710909",
        ),
    ),
    IgnoreUpdate(),
    state=Subscription.PLANS,
    getter=plans_getter,
)

duration = Window(
    Banner(BannerName.TARIFF),
    I18nFormat("msg-subscription-duration"),
    Group(
        Select(
            text=I18nFormat(
                "btn-subscription.duration",
                period=F["item"]["period"],
                final_amount=F["item"]["final_amount"],
                discount_percent=F["item"]["discount_percent"],
                original_amount=F["item"]["original_amount"],
                currency=F["item"]["currency"],
            ),
            id=f"{PAYMENT_PREFIX}select_duration",
            item_id_getter=lambda item: item["days"],
            items="durations",
            type_factory=int,
            on_click=on_duration_select,
        ),
        width=2,
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-subscription.back-plans"),
            id=f"{PAYMENT_PREFIX}back_plans",
            state=Subscription.PLANS,
            when=~F["only_single_plan"],
        ),
    ),
    *back_main_menu_button,
    IgnoreUpdate(),
    state=Subscription.DURATION,
    getter=duration_getter,
)

payment_method = Window(
    Banner(BannerName.TARIFF),
    I18nFormat("msg-subscription-payment-method"),
    Column(
        Select(
            text=I18nFormat(
                "btn-subscription.payment-method",
                payment_label=F["item"]["payment_label"],
                final_amount=F["item"]["final_amount"],
                original_amount=F["item"]["original_amount"],
                discount_percent=F["item"]["discount_percent"],
                currency=F["item"]["currency"],
            ),
            id=f"{PAYMENT_PREFIX}select_payment_method",
            item_id_getter=lambda item: item["id"],
            items="payment_methods",
            type_factory=str,
            on_click=on_payment_method_select,
        ),
    ),
    Row(
        IconSwitchTo(
            text=I18nFormat("btn-back.general"),
            id=f"{PAYMENT_PREFIX}back_duration",
            state=Subscription.DURATION,
            when=~F["only_single_duration"],
            icon_custom_emoji_id="5258236805890710909",
        ),
        IconSwitchTo(
            text=I18nFormat("btn-back.general"),
            id=f"{PAYMENT_PREFIX}back_plans",
            state=Subscription.PLANS,
            when=F["only_single_duration"] & ~F["only_single_plan"],
            icon_custom_emoji_id="5258236805890710909",
        ),
        IconStart(
            text=I18nFormat("btn-back.general"),
            id=f"{PAYMENT_PREFIX}back_main",
            state=MainMenu.MAIN,
            mode=StartMode.RESET_STACK,
            when=F["only_single_duration"] & F["only_single_plan"],
            icon_custom_emoji_id="5258236805890710909",
        ),
    ),
    IgnoreUpdate(),
    state=Subscription.PAYMENT_METHOD,
    getter=payment_method_getter,
)

confirm = Window(
    Banner(BannerName.TARIFF),
    I18nFormat("msg-subscription-confirm"),
    Row(
        Url(
            text=I18nFormat("btn-subscription.pay"),
            url=Format("{url}"),
            when=F["url"],
            style=Style(ButtonStyle.SUCCESS),
        ),
        Button(
            text=I18nFormat("btn-subscription.get"),
            id=f"{PAYMENT_PREFIX}get",
            on_click=on_get_subscription,
            when=~F["url"],
            style=Style(ButtonStyle.SUCCESS),
        ),
    ),
    Row(
        IconSwitchTo(
            text=I18nFormat("btn-back.general"),
            id=f"{PAYMENT_PREFIX}back_payment_method",
            state=Subscription.PAYMENT_METHOD,
            when=~F["only_single_gateway"] & ~F["is_free"],
            icon_custom_emoji_id="5258236805890710909",
        ),
        IconSwitchTo(
            text=I18nFormat("btn-back.general"),
            id=f"{PAYMENT_PREFIX}back_duration",
            state=Subscription.DURATION,
            when=(F["only_single_gateway"] | F["is_free"]) & ~F["only_single_duration"],
            icon_custom_emoji_id="5258236805890710909",
        ),
        IconSwitchTo(
            text=I18nFormat("btn-back.general"),
            id=f"{PAYMENT_PREFIX}back_plans",
            state=Subscription.PLANS,
            when=(F["only_single_gateway"] | F["is_free"])
            & F["only_single_duration"]
            & ~F["only_single_plan"],
            icon_custom_emoji_id="5258236805890710909",
        ),
        IconStart(
            text=I18nFormat("btn-back.general"),
            id=f"{PAYMENT_PREFIX}back_main",
            state=MainMenu.MAIN,
            mode=StartMode.RESET_STACK,
            when=(F["only_single_gateway"] | F["is_free"])
            & F["only_single_duration"]
            & F["only_single_plan"],
            icon_custom_emoji_id="5258236805890710909",
        ),
    ),
    IgnoreUpdate(),
    state=Subscription.CONFIRM,
    getter=confirm_getter,
)

success_payment = Window(
    Banner(BannerName.SUBSCRIPTION),
    I18nFormat("msg-subscription-success"),
    Row(
        IconStart(
            text=I18nFormat("btn-instruction.ios"),
            id=f"{PAYMENT_PREFIX}instruction_ios",
            state=MainMenu.INSTRUCTION_IOS,
            mode=StartMode.RESET_STACK,
            when=F["connectable"],
            icon_custom_emoji_id="5775870512127283512",
        ),
        IconStart(
            text=I18nFormat("btn-instruction.android"),
            id=f"{PAYMENT_PREFIX}instruction_android",
            state=MainMenu.INSTRUCTION_ANDROID,
            mode=StartMode.RESET_STACK,
            when=F["connectable"],
            icon_custom_emoji_id="5100720104375583787",
        ),
    ),
    Row(
        IconStart(
            text=I18nFormat("btn-instruction.windows"),
            id=f"{PAYMENT_PREFIX}instruction_windows",
            state=MainMenu.INSTRUCTION_WINDOWS,
            mode=StartMode.RESET_STACK,
            when=F["connectable"],
            icon_custom_emoji_id="4976701317385814718",
        ),
        IconStart(
            text=I18nFormat("btn-instruction.macos"),
            id=f"{PAYMENT_PREFIX}instruction_macos",
            state=MainMenu.INSTRUCTION_MACOS,
            mode=StartMode.RESET_STACK,
            when=F["connectable"],
            icon_custom_emoji_id="5775870512127283512",
        ),
    ),
    *back_main_menu_button,
    IgnoreUpdate(),
    state=Subscription.SUCCESS,
    getter=success_payment_getter,
)

success_trial = Window(
    Banner(BannerName.SUBSCRIPTION),
    I18nFormat("msg-subscription-trial"),
    Row(
        IconStart(
            text=I18nFormat("btn-instruction.ios"),
            id=f"{PAYMENT_PREFIX}instruction_trial_ios",
            state=MainMenu.INSTRUCTION_IOS,
            mode=StartMode.RESET_STACK,
            when=F["connectable"],
            icon_custom_emoji_id="5775870512127283512",
        ),
        IconStart(
            text=I18nFormat("btn-instruction.android"),
            id=f"{PAYMENT_PREFIX}instruction_trial_android",
            state=MainMenu.INSTRUCTION_ANDROID,
            mode=StartMode.RESET_STACK,
            when=F["connectable"],
            icon_custom_emoji_id="5100720104375583787",
        ),
    ),
    Row(
        IconStart(
            text=I18nFormat("btn-instruction.windows"),
            id=f"{PAYMENT_PREFIX}instruction_trial_windows",
            state=MainMenu.INSTRUCTION_WINDOWS,
            mode=StartMode.RESET_STACK,
            when=F["connectable"],
            icon_custom_emoji_id="4976701317385814718",
        ),
        IconStart(
            text=I18nFormat("btn-instruction.macos"),
            id=f"{PAYMENT_PREFIX}instruction_trial_macos",
            state=MainMenu.INSTRUCTION_MACOS,
            mode=StartMode.RESET_STACK,
            when=F["connectable"],
            icon_custom_emoji_id="5775870512127283512",
        ),
    ),
    *back_main_menu_button,
    IgnoreUpdate(),
    state=Subscription.TRIAL,
    getter=getter_connect,
)

failed = Window(
    Banner(BannerName.SUBSCRIPTION),
    I18nFormat("msg-subscription-failed"),
    *back_main_menu_button,
    IgnoreUpdate(),
    state=Subscription.FAILED,
)

router = Dialog(
    subscription,
    plan,
    plans,
    duration,
    payment_method,
    confirm,
    success_payment,
    success_trial,
    failed,
    on_start=on_subscription_start,
)
