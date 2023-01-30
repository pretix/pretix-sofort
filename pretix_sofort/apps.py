from django.apps import AppConfig
from django.utils.translation import gettext_lazy
from . import __version__


class PluginApp(AppConfig):
    name = 'pretix_sofort'
    verbose_name = 'Sofort payment'

    class PretixPluginMeta:
        name = 'SOFORT'
        author = 'Raphael Michel'
        category = 'PAYMENT'
        description = gettext_lazy('Accept payments through Sofort, a payment method offered by Klarna.')
        visible = True
        picture = "pretix_sofort/logo.png"
        version = __version__
        compatibility = "pretix>=4.16.0"

    def ready(self):
        from . import signals  # NOQA


