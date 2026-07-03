from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from pretix.base.signals import (
    logentry_display, register_payment_providers, )
from .payment import Sofort


@receiver(register_payment_providers, dispatch_uid="payment_sofort")
def register_payment_provider(sender, **kwargs):
    return [Sofort]


@receiver(signal=logentry_display, dispatch_uid="sofort_logentry_display")
def pretixcontrol_logentry_display(sender, logentry, **kwargs):
    if logentry.action_type != "pretix_sofort.sofort.event":
        return

    status_codes = {
        "untraceable": _("Transaction started, no tracing possible"),
        "refunded": _("Transaction refunded"),
        "loss": _("Money not received"),
        "pending": _("Money not yet received"),
        "received": _("Money received"),
    }

    return _("Sofort reported a status notification: {status}").format(
        status=status_codes.get(logentry.parsed_data.get("status"), "?")
    )
