import hashlib
import json
import logging
import urllib
from collections import OrderedDict
from urllib.parse import parse_qs

import requests
from django import forms
from django.core import signing
from django.template.loader import get_template
from django.utils.translation import ugettext_lazy as _

from pretix.base.payment import BasePaymentProvider
from pretix.multidomain.urlreverse import build_absolute_uri
from . import sofort
from .models import ReferencedSofortTransaction

logger = logging.getLogger(__name__)


class RefundForm(forms.Form):
    auto_refund = forms.ChoiceField(
        initial='auto',
        label=_('Refund automatically?'),
        choices=(
            ('auto', _('Automatically refund charge with Sofort')),
            ('manual', _('Do not send refund instruction to Sofort, only mark as refunded in pretix'))
        ),
        widget=forms.RadioSelect,
    )


class Sofort(BasePaymentProvider):
    identifier = 'sofort'
    verbose_name = _('Sofort')

    @property
    def settings_form_fields(self):
        d = OrderedDict(
            [
                ('customer_id',
                 forms.CharField(
                     label=_('Customer ID'),
                 )),
                ('api_key',
                 forms.CharField(
                     label=_('API key'),
                 )),
                ('project_id',
                 forms.CharField(
                     label=_('Project ID'),
                 )),
                # TODO: Beneficiary?
            ] + list(super().settings_form_fields.items())
        )
        d.move_to_end('_enabled', False)
        return d

    def payment_form_render(self, request) -> str:
        template = get_template('pretix_sofort/checkout_payment_form.html')
        ctx = {'request': request, 'event': self.event, 'settings': self.settings}
        return template.render(ctx)

    def checkout_confirm_render(self, request) -> str:
        template = get_template('pretix_sofort/checkout_payment_confirm.html')
        ctx = {'request': request, 'event': self.event, 'settings': self.settings}
        return template.render(ctx)

    def checkout_prepare(self, request, total):
        return True

    def payment_is_valid_session(self, request):
        return True

    def _api_call(self, payload):
        return requests.post(
            'https://api.sofort.com/api/xml',
            data=payload,
            auth=(self.settings.get('customer_id'), self.settings.get('api_key')),
            headers={
                'Content-Type': 'application/xml; charset=UTF-8',
                'Accept': 'application/xml; charset=UTF-8',
            }
        ).content

    def redirect(self, request, url):
        if request.session.get('iframe_session', False):
            signer = signing.Signer(salt='safe-redirect')
            return (
                build_absolute_uri(request.event, 'plugins:pretix_sofort:redirect') + '?url=' +
                urllib.parse.quote(signer.sign(url))
            )
        else:
            return str(url)

    def payment_perform(self, request, order) -> str:
        request.session['sofort_order_secret'] = order.secret
        shash = hashlib.sha1(order.secret.lower().encode()).hexdigest()
        r = sofort.MultiPay(
            project_id=self.settings.get('project_id'),
            amount=order.total,
            currency_code=self.event.currency,
            reasons=[
                order.full_code,
                '-TRANSACTION-'
            ],
            user_variables=[order.full_code],
            success_url=build_absolute_uri(self.event, 'plugins:pretix_sofort:return', kwargs={
                'order': order.code,
                'hash': shash,
            }) + '?state=success&transaction=-TRANSACTION-',
            abort_url=build_absolute_uri(self.event, 'plugins:pretix_sofort:return', kwargs={
                'order': order.code,
                'hash': shash,
            }) + '?state=abort&transaction=-TRANSACTION-',
            timeout_url=build_absolute_uri(self.event, 'plugins:pretix_sofort:return', kwargs={
                'order': order.code,
                'hash': shash,
            }) + '?state=timeout&transaction=-TRANSACTION-',
            notification_urls=[
                build_absolute_uri(self.event, 'plugins:pretix_sofort:webhook')
            ],
        )
        # TODO: Error handling
        trans = sofort.NewTransaction.from_xml(self._api_call(r.to_xml()))
        ReferencedSofortTransaction.objects.get_or_create(order=order, reference=trans.transaction)
        order.payment_info = json.dumps({'transaction': trans.transaction, 'status': 'initiated'})
        order.save(update_fields=['payment_info'])
        return self.redirect(request, trans.payment_url)

    def order_pending_render(self, request, order) -> str:
        retry = True
        try:
            if order.payment_info and json.loads(order.payment_info)['paymentState'] == 'PENDING':
                retry = False
        except KeyError:
            pass
        template = get_template('pretix_sofort/pending.html')
        ctx = {'request': request, 'event': self.event, 'settings': self.settings,
               'retry': retry, 'order': order}
        return template.render(ctx)

    def order_control_render(self, request, order) -> str:
        if order.payment_info:
            payment_info = json.loads(order.payment_info)
        else:
            payment_info = None
        template = get_template('pretix_sofort/control.html')
        ctx = {'request': request, 'event': self.event, 'settings': self.settings,
               'payment_info': payment_info, 'order': order, 'provname': self.verbose_name}
        return template.render(ctx)

    def order_can_retry(self, order):
        return True

    @property
    def refund_available(self):
        return False  # TODO: Implement?

    def order_control_refund_render(self, order, request) -> str:
        if self.refund_available:
            template = get_template('pretix_sofort/control_refund.html')
            ctx = {
                'request': request,
                'form': self._refund_form(request),
            }
            return template.render(ctx)
        else:
            return super().order_control_refund_render(order, request)

    def order_control_refund_perform(self, request, order) -> "bool|str":
        pass
