import json
import logging
from typing import Union

from django.http import HttpRequest
from django.template.loader import get_template
from django.utils.translation import gettext_lazy as _

from pretix.base.models import OrderPayment, OrderRefund
from pretix.base.payment import BaseHistoricalPaymentProvider

logger = logging.getLogger(__name__)


class Sofort(BaseHistoricalPaymentProvider):
    identifier = "sofort"
    verbose_name = _("Sofort")
    public_name = _("SOFORT (instant bank transfer)")

    def payment_control_render(self, request: HttpRequest, payment: OrderPayment):
        template = get_template("pretix_sofort/control.html")
        ctx = {
            "request": request,
            "event": self.event,
            "settings": self.settings,
            "payment_info": payment.info_data,
            "order": payment.order,
            "provname": self.verbose_name,
        }
        return template.render(ctx)

    def order_can_retry(self, order):
        return True

    def payment_refund_supported(self, payment: OrderPayment):
        return True

    def payment_partial_refund_supported(self, payment: OrderPayment):
        return True

    def shred_payment_info(self, obj: Union[OrderPayment, OrderRefund]):
        d = obj.info_data
        new = {"_shreded": True}
        for k in (
            "payment_method",
            "amount",
            "status_reason",
            "time",
            "exchange_rate",
            "transaction",
            "currency_code",
            "transaction",
            "project_id",
            "costs",
            "status_modified",
            "status",
            "reasons",
            "language_code",
        ):
            if k in d:
                new[k] = d[k]
        obj.info_data = new
        obj.save(update_fields=["info"])
        for le in (
            obj.order.all_logentries()
            .filter(action_type="pretix_sofort.sofort.event")
            .exclude(data="")
        ):
            d = le.parsed_data
            new = {"_shreded": True}
            for k in (
                "payment_method",
                "amount",
                "status_reason",
                "time",
                "exchange_rate",
                "transaction",
                "currency_code",
                "transaction",
                "project_id",
                "costs",
                "status_modified",
                "status",
                "reasons",
                "language_code",
            ):
                if k in d:
                    new[k] = d[k]
            le.data = json.dumps(new)
            le.shredded = True
            le.save(update_fields=["data", "shredded"])
