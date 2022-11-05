import hashlib
import json
import logging
import urllib.parse
from decimal import Decimal

from django.contrib import messages
from django.core import signing
from django.db.models import Sum
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt

from pretix.base.models import Order, Quota, OrderPayment, OrderRefund
from pretix.base.payment import PaymentException
from pretix.multidomain.urlreverse import eventreverse, build_absolute_uri
from . import sofort
from .models import ReferencedSofortTransaction
from .payment import Sofort

logger = logging.getLogger('pretix_sofort')


@csrf_exempt
def webhook(request, *args, **kwargs):
    try:
        sn = sofort.StatusNotification.from_xml(request.body)
        rso = ReferencedSofortTransaction.objects.select_related('order', 'order__event').get(reference=sn.transaction)
        process_result(request, rso, sn.transaction, log=True, warn=False)
        return HttpResponse("OK")
    except PaymentException as e:
        logger.exception('Failure during sofort payment: {}'.format(e.message))
        return HttpResponse("FAIL", status=500)
    except sofort.SofortError as e:
        logger.exception('Failure during sofort payment: {}'.format(e.message))
        return HttpResponse("FAIL", status=500)
    except ReferencedSofortTransaction.DoesNotExist:
        raise Http404("Unknown transaction.")
    except IOError as e:
        logger.exception('Failure during sofort payment: {}'.format(e.message))
        return HttpResponse("FAIL", status=500)


@xframe_options_exempt
def redirect_view(request, *args, **kwargs):
    try:
        data = signing.loads(request.GET.get('data', ''), salt='safe-redirect')
    except signing.BadSignature:
        return HttpResponseBadRequest('Invalid parameter')

    if 'go' in request.GET:
        if 'session' in data:
            for k, v in data['session'].items():
                request.session[k] = v
        return redirect(data['url'])
    else:
        params = request.GET.copy()
        params['go'] = '1'
        r = render(request, 'pretix_sofort/redirect.html', {
            'url': build_absolute_uri(request.event, 'plugins:pretix_sofort:redirect') + '?' + urllib.parse.urlencode(params),
        })
        r._csp_ignore = True
        return r


def process_result(request, rso, transaction, log=False, warn=True):
    s = Sofort(request.event)
    r = sofort.TransactionRequest(transactions=[transaction])

    try:
        trans = sofort.Transactions.from_xml(s._api_call(r.to_xml()))
    except sofort.SofortError as e:
        logger.exception('Failure during sofort payment: {}'.format(e.message))
        raise PaymentException(_('Sofort reported an error: {}').format(e.message))
    except IOError:
        logger.exception('Failure during sofort payment.')
        raise PaymentException(_('We had trouble communicating with Sofort. Please try again and get in touch '
                                 'with us if this problem persists.'))

    if not rso.payment:
        rso.payment = rso.order.payments.filter(
            info__icontains=transaction,
            provider__startswith='sofort',
        ).last()
        rso.save()

    if not rso.payment:
        rso.payment = rso.order.payments.create(
            state=OrderPayment.PAYMENT_STATE_CREATED,
            provider='sofort',
            amount=trans.details[0]['amount'],
            info=json.dumps({
                {'transaction': transaction, 'status': 'initiated'}
            }),
        )
        rso.save()

    if trans.details:
        td = trans.details[0]
        rso.payment.info = td.to_json(no_sepa_data=True)
        rso.payment.save(update_fields=['info'])
        rso.order.log_action('pretix_sofort.sofort.event', data=td.to_data(no_sepa_data=True))

        if td.status in ('pending', 'received', 'untraceable') and rso.payment.state in (
                OrderPayment.PAYMENT_STATE_CREATED, OrderPayment.PAYMENT_STATE_PENDING,
                OrderPayment.PAYMENT_STATE_FAILED):
            try:
                rso.payment.state = Order.STATUS_PENDING
                rso.payment.confirm()
                rso.payment.refresh_from_db()
            except Quota.QuotaExceededException:
                raise PaymentException(_('Your payment could not be handled as the event sold out in the meantime. '
                                         'Please contact the organizer for more information.'))
        elif td.status == 'refunded' and rso.payment.state == OrderPayment.PAYMENT_STATE_CONFIRMED:
            known_sum = rso.payment.refunds.filter(
                state__in=(OrderRefund.REFUND_STATE_DONE, OrderRefund.REFUND_STATE_TRANSIT,
                           OrderRefund.REFUND_STATE_CREATED, OrderRefund.REFUND_SOURCE_EXTERNAL)
            ).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
            total_refunded_amount = Decimal(td.amount_refunded)
            if known_sum < total_refunded_amount:
                rso.payment.create_external_refund(
                    amount=total_refunded_amount - known_sum
                )
        elif td.status == 'loss':
            rso.payment.state = OrderPayment.PAYMENT_STATE_FAILED
            rso.payment.save()
            rso.payment.order.log_action('pretix.event.order.payment.failed', {
                'local_id': rso.payment.local_id,
                'provider': rso.payment.provider,
                'info': str(td)
            })
            if rso.order.pending_sum > 0:
                rso.order.status = Order.STATUS_PENDING
                rso.order.save()
        elif warn:
            messages.error(request, _('The payment process has failed. You can click below to try again.'))
    elif warn:
        messages.warning(
            request, _('Your payment has been started processing and will take a while to complete. We will '
                       'send you an email once your payment is completed. If this takes longer than expected, '
                       'contact the event organizer.')
        )


@method_decorator(xframe_options_exempt, 'dispatch')
class ReturnView(View):
    def dispatch(self, request, *args, **kwargs):
        try:
            self.order = request.event.orders.get(code=kwargs['order'])
            if hashlib.sha1(self.order.secret.lower().encode()).hexdigest() != kwargs['hash'].lower():
                raise Http404()
        except Order.DoesNotExist:
            # Do a hash comparison as well to harden timing attacks
            if 'abcdefghijklmnopq'.lower() == hashlib.sha1('abcdefghijklmnopq'.encode()).hexdigest():
                raise Http404()
            else:
                raise Http404()
        return super().dispatch(request, *args, **kwargs)

    @cached_property
    def pprov(self):
        return self.request.event.get_payment_providers()[self.order.payment_provider]

    def get(self, request, *args, **kwargs):
        if request.GET.get('state') in ('abort', 'timeout'):
            messages.error(self.request, _('The payment process was canceled. You can click below to try again.'))
            return self._redirect_to_order()

        if self.order.status == Order.STATUS_PAID:
            return self._redirect_to_order()

        try:
            rso = ReferencedSofortTransaction.objects.get(
                reference=request.GET.get("transaction"), order=self.order
            )
        except ReferencedSofortTransaction.DoesNotExist:
            messages.error(self.request, _('Sorry, there was an error in the payment process.'))
            return redirect(eventreverse(self.request.event, 'presale:event.index'))

        try:
            process_result(request, rso, request.GET.get("transaction"), log=False, warn=True)
        except PaymentException as e:
            messages.error(self.request, str(e))
        return self._redirect_to_order()

    def _redirect_to_order(self):
        if self.request.session.get('payment_sofort_order_secret') != self.order.secret:
            messages.error(self.request, _('Sorry, there was an error in the payment process. Please check the link '
                                           'in your emails to continue.'))
            return redirect(eventreverse(self.request.event, 'presale:event.index'))

        return redirect(eventreverse(self.request.event, 'presale:event.order', kwargs={
            'order': self.order.code,
            'secret': self.order.secret
        }) + ('?paid=yes' if self.order.status == Order.STATUS_PAID else ''))
