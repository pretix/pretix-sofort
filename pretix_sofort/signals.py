import json

from django.dispatch import receiver
from django.template.loader import get_template
from django.utils.translation import ugettext_lazy as _

from pretix.base.signals import register_payment_providers, logentry_display, requiredaction_display
from .payment import Sofort


@receiver(register_payment_providers, dispatch_uid="payment_sofort")
def register_payment_provider(sender, **kwargs):
    return [Sofort]


@receiver(signal=logentry_display, dispatch_uid="wirecard_logentry_display")
def pretixcontrol_logentry_display(sender, logentry, **kwargs):
    if logentry.action_type != 'pretix_sofort.sofort.event':
        return

    # TODO: implement

    return _('Sofort reported an event: {}').format('')


@receiver(signal=requiredaction_display, dispatch_uid="sofort_requiredaction_display")
def pretixcontrol_action_display(sender, action, request, **kwargs):
    if not action.action_type.startswith('pretix_sofort'):
        return

    data = json.loads(action.data)

    if action.action_type == 'pretixsofort.sofort.overpaid':
        template = get_template('pretix_sofort/action_overpaid.html')

    ctx = {'data': data, 'event': sender, 'action': action}
    return template.render(ctx, request)
