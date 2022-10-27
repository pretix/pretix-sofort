from django.apps import AppConfig
from django.utils.translation import gettext_lazy

__version__ = '1.3.7'


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

    def ready(self):
        from . import signals  # NOQA


default_app_config = 'pretix_sofort.PluginApp'
