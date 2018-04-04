import hashlib
import json
import logging

from django.contrib import messages
from django.core import signing
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.utils.translation import ugettext_lazy as _
from django.views import View
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from pretix.base.models import Order, Quota, RequiredAction
from pretix.base.services.orders import mark_order_paid, mark_order_refunded
from pretix.control.permissions import event_permission_required
from pretix.multidomain.urlreverse import eventreverse

from . import sofort
from .models import ReferencedSofortTransaction
from .payment import Sofort

logger = logging.getLogger('pretix_sofort')


@csrf_exempt
def webhook(request, *args, **kwargs):
    # TODO: Error handling
    sn = sofort.StatusNotification.from_xml(request.body)

    try:
        rso = ReferencedSofortTransaction.objects.select_related('order', 'order__event').get(reference=sn.transaction)
        process_result(request, rso.order, sn.transaction, log=True, warn=False)
        return HttpResponse("OK")
    except ReferencedSofortTransaction.DoesNotExist:
        raise Http404("Unknown transaction.")


@xframe_options_exempt
def redirect_view(request, *args, **kwargs):
    signer = signing.Signer(salt='safe-redirect')
    try:
        url = signer.unsign(request.GET.get('url', ''))
    except signing.BadSignature:
        return HttpResponseBadRequest('Invalid parameter')

    r = render(request, 'pretix_sofort/redirect.html', {
        'url': url,
    })
    r._csp_ignore = True
    return r


def process_result(request, order, transaction, log=False, warn=True):
    # TODO: Error handling
    s = Sofort(request.event)
    r = sofort.TransactionRequest(transactions=[transaction])
    trans = sofort.Transactions.from_xml(s._api_call(r.to_xml()))
    if trans.details:
        td = trans.details[0]
        order.payment_info = td.to_json(no_sepa_data=True)
        order.save(update_fields=['payment_info'])
        order.log_action('pretix_sofort.sofort.event', data=td.to_data(no_sepa_data=True))

        if td.status in ('pending', 'pending', 'untraceable') and order.status in (Order.STATUS_PENDING,
                                                                                   Order.STATUS_EXPIRED):
            try:
                order.refresh_from_db()
                mark_order_paid(order, user=None, provider='sofort')
            except Quota.QuotaExceededException:
                if not RequiredAction.objects.filter(event=request.event, action_type='pretix_sofort.sofort.overpaid',
                                                     data__icontains=order.code).exists():
                    RequiredAction.objects.create(
                        event=request.event, action_type='pretix_sofort.sofort.overpaid', data=json.dumps({
                            'order': order.code,
                            'transaction': transaction,
                        })
                    )
                if warn:
                    messages.error(request, _('Your payment could not be handled as the event sold out in the meantime.'
                                              ' Please contact the organizer for more information.'))
        elif td.status == 'refunded' and order.status == Order.STATUS_PAID:
            RequiredAction.objects.create(
                event=request.event, action_type='pretix_sofort.sofort.refund', data=json.dumps({
                    'order': order.code,
                    'transaction': transaction
                })
            )
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
            ReferencedSofortTransaction.objects.get(
                reference=request.GET.get("transaction"), order=self.order
            )
        except ReferencedSofortTransaction:
            messages.error(self.request, _('Sorry, there was an error in the payment process.'))
            return redirect(eventreverse(self.request.event, 'presale:event.index'))

        process_result(request, self.order, request.GET.get("transaction"), log=False, warn=True)
        return self._redirect_to_order()

    def _redirect_to_order(self):
        if self.request.session.get('sofort_order_secret') != self.order.secret:
            messages.error(self.request, _('Sorry, there was an error in the payment process. Please check the link '
                                           'in your emails to continue.'))
            return redirect(eventreverse(self.request.event, 'presale:event.index'))

        return redirect(eventreverse(self.request.event, 'presale:event.order', kwargs={
            'order': self.order.code,
            'secret': self.order.secret
        }) + ('?paid=yes' if self.order.status == Order.STATUS_PAID else ''))


@event_permission_required('can_change_orders')
@require_POST
def refund(request, **kwargs):
    with transaction.atomic():
        action = get_object_or_404(RequiredAction, event=request.event, pk=kwargs.get('id'),
                                   action_type='pretix_sofort.sofort.refund', done=False)
        data = json.loads(action.data)
        action.done = True
        action.user = request.user
        action.save()
        order = get_object_or_404(Order, event=request.event, code=data['order'])
        if order.status != Order.STATUS_PAID:
            messages.error(request, _('The order cannot be marked as refunded as it is not marked as paid!'))
        else:
            mark_order_refunded(order, user=request.user)
            messages.success(
                request, _('The order has been marked as refunded and the issue has been marked as resolved!')
            )

    return redirect(reverse('control:event.order', kwargs={
        'organizer': request.event.organizer.slug,
        'event': request.event.slug,
        'code': data['order']
    }))
