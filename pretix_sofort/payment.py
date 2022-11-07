import hashlib
import json
import logging
import urllib
from collections import OrderedDict
from urllib.parse import parse_qs

import requests
from django import forms
from django.core import signing
from django.http import HttpRequest
from django.template.loader import get_template
from django.utils.translation import gettext_lazy as _
from typing import Union

from requests import HTTPError

from pretix.base.models import Order, OrderPayment, OrderRefund
from pretix.base.payment import BasePaymentProvider, PaymentException
from pretix.multidomain.urlreverse import build_absolute_uri

from . import sofort
from .models import ReferencedSofortTransaction

logger = logging.getLogger(__name__)


class Sofort(BasePaymentProvider):
    identifier = 'sofort'
    verbose_name = _('Sofort')
    public_name = _('SOFORT (instant bank transfer)')
    abort_pending_allowed = False

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
        r = requests.post(
            'https://api.sofort.com/api/xml',
            data=payload,
            auth=(self.settings.get('customer_id'), self.settings.get('api_key')),
            headers={
                'Content-Type': 'application/xml; charset=UTF-8',
                'Accept': 'application/xml; charset=UTF-8',
            }
        )
        if r.status_code >= 500:
            raise HTTPError()
        return r.content

    def redirect(self, request, url):
        if request.session.get('iframe_session', False):
            return (
                build_absolute_uri(request.event, 'plugins:pretix_sofort:redirect') +
                '?data=' + signing.dumps({
                    'url': url,
                    'session': {
                        'payment_sofort_order_secret': request.session['payment_sofort_order_secret'],
                    },
                }, salt='safe-redirect')
            )
        else:
            return str(url)

    def execute_payment(self, request: HttpRequest, payment: OrderPayment):
        request.session['payment_sofort_order_secret'] = payment.order.secret
        shash = hashlib.sha1(payment.order.secret.lower().encode()).hexdigest()
        r = sofort.MultiPay(
            project_id=self.settings.get('project_id'),
            amount=payment.amount,
            currency_code=self.event.currency,
            reasons=[
                payment.order.full_code,
                '-TRANSACTION-'
            ],
            user_variables=[payment.order.full_code],
            success_url=build_absolute_uri(self.event, 'plugins:pretix_sofort:return', kwargs={
                'order': payment.order.code,
                'hash': shash,
            }) + '?state=success&transaction=-TRANSACTION-',
            abort_url=build_absolute_uri(self.event, 'plugins:pretix_sofort:return', kwargs={
                'order': payment.order.code,
                'hash': shash,
            }) + '?state=abort&transaction=-TRANSACTION-',
            timeout_url=build_absolute_uri(self.event, 'plugins:pretix_sofort:return', kwargs={
                'order': payment.order.code,
                'hash': shash,
            }) + '?state=timeout&transaction=-TRANSACTION-',
            notification_urls=[
                build_absolute_uri(self.event, 'plugins:pretix_sofort:webhook')
            ],
        )
        try:
            trans = sofort.NewTransaction.from_xml(self._api_call(r.to_xml()))
        except sofort.SofortError as e:
            logger.exception('Failure during sofort payment: {}'.format(e.message))
            raise PaymentException(_('Sofort reported an error: {}').format(e.message))
        except IOError:
            logger.exception('Failure during sofort payment.')
            raise PaymentException(_('We had trouble communicating with Sofort. Please try again and get in touch '
                                     'with us if this problem persists.'))
        ReferencedSofortTransaction.objects.get_or_create(order=payment.order, reference=trans.transaction,
                                                          payment=payment)
        payment.info_data = {'transaction': trans.transaction, 'status': 'initiated'}
        payment.save(update_fields=['info'])
        return self.redirect(request, trans.payment_url)

    def payment_pending_render(self, request: HttpRequest, payment: OrderPayment):
        retry = True
        try:
            if payment.info_data.get('paymentState') == 'PENDING':
                retry = False
        except KeyError:
            pass
        template = get_template('pretix_sofort/pending.html')
        ctx = {'request': request, 'event': self.event, 'settings': self.settings,
               'retry': retry, 'order': payment.order}
        return template.render(ctx)

    def payment_control_render(self, request: HttpRequest, payment: OrderPayment):
        template = get_template('pretix_sofort/control.html')
        ctx = {'request': request, 'event': self.event, 'settings': self.settings,
               'payment_info': payment.info_data, 'order': payment.order, 'provname': self.verbose_name}
        return template.render(ctx)

    def order_can_retry(self, order):
        return True

    def payment_refund_supported(self, payment: OrderPayment):
        return True

    def payment_partial_refund_supported(self, payment: OrderPayment):
        return True

    def _refund(self, refund):
        r = sofort.Refunds(refunds=[
            sofort.Refund(
                transaction=refund.payment.info_data.get('transaction'),
                amount=refund.amount,
                comment=refund.order.full_code,
                reason_1=refund.order.full_code,
                reason_2=refund.payment.info_data.get('transaction')
            )
        ])
        try:
            sofort.Refunds.from_xml(self._api_call(r.to_xml()))
        except sofort.SofortError as e:
            logger.exception('Failure during sofort payment: {}'.format(e.message))
            raise PaymentException(_('Sofort reported an error: {}').format(e.message))
        except IOError:
            logger.exception('Failure during sofort payment.')
            raise PaymentException(_('We had trouble communicating with Sofort. Please try again and get in touch '
                                     'with us if this problem persists.'))

    def execute_refund(self, refund: OrderRefund):
        try:
            self._refund(refund)
        except requests.exceptions.RequestException as e:
            logger.exception('Sofort error: %s' % str(e))
            raise PaymentException(_('We had trouble communicating with Sofort. Please try again and contact '
                                     'support if the problem persists.'))
        else:
            refund.done()

    def shred_payment_info(self, obj: Union[OrderPayment, OrderRefund]):
        d = obj.info_data
        new = {
            '_shreded': True
        }
        for k in ('payment_method', 'amount', 'status_reason', 'time', 'exchange_rate', 'transaction',
                  'currency_code', 'transaction', 'project_id', 'costs', 'status_modified', 'status', 'reasons',
                  'language_code'):
            if k in d:
                new[k] = d[k]
        obj.info_data = new
        obj.save(update_fields=['info'])
        for le in obj.order.all_logentries().filter(action_type="pretix_sofort.sofort.event").exclude(data=""):
            d = le.parsed_data
            new = {
                '_shreded': True
            }
            for k in ('payment_method', 'amount', 'status_reason', 'time', 'exchange_rate', 'transaction',
                      'currency_code', 'transaction', 'project_id', 'costs', 'status_modified', 'status', 'reasons',
                      'language_code'):
                if k in d:
                    new[k] = d[k]
            le.data = json.dumps(new)
            le.shredded = True
            le.save(update_fields=['data', 'shredded'])
