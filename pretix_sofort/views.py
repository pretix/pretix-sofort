import hashlib
import logging

from django.core import signing
from django.http import HttpResponseBadRequest, Http404
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.views import View
from django.views.decorators.clickjacking import xframe_options_exempt

from pretix.base.models import Order

logger = logging.getLogger('pretix_sofort')


def webhook(request):
    pass


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
        messages.error(
            request, _('The payment failed without an error message. '
                       'You can click below to try again.')
        )
        return self._redirect_to_order()

    def post(self, request, *args, **kwargs):
        if not validate_fingerprint(request, self.pprov):
            messages.error(self.request, _('Sorry, we could not validate the payment result. Please try again or '
                                           'contact the event organizer to check if your payment was successful.'))
            return self._redirect_to_order()

        self.order.log_action('pretix_wirecard.wirecard.event', data=dict(request.POST.items()))
        if request.POST.get('paymentState') == 'CANCEL':
            messages.error(self.request, _('The payment process was canceled. You can click below to try again.'))
            return self._redirect_to_order()

        if request.POST.get('paymentState') == 'FAILURE':
            messages.error(
                self.request, _('The payment failed with the following message: {message}. '
                                'You can click below to try again.').format(message=request.POST.get('message')))
            return self._redirect_to_order()

        if request.POST.get('paymentState') == 'PENDING':
            messages.warning(
                self.request, _('Your payment has been started processing and will take a while to complete. We will '
                                'send you an email once your payment is completed. If this takes longer than expected, '
                                'contact the event organizer.')
            )
            return self._redirect_to_order()

        process_result(request, self.order, self.pprov)
        return self._redirect_to_order()

    def _redirect_to_order(self):
        if self.request.session.get('wirecard_order_secret') != self.order.secret:
            messages.error(self.request, _('Sorry, there was an error in the payment process. Please check the link '
                                           'in your emails to continue.'))
            return redirect(eventreverse(self.request.event, 'presale:event.index'))

        return redirect(eventreverse(self.request.event, 'presale:event.order', kwargs={
            'order': self.order.code,
            'secret': self.order.secret
        }) + ('?paid=yes' if self.order.status == Order.STATUS_PAID else ''))
