from django.apps import AppConfig
from django.utils.translation import ugettext_lazy

__version__ = '1.0.0'


class PluginApp(AppConfig):
    name = 'pretix_sofort'
    verbose_name = 'Sofort payment'

    class PretixPluginMeta:
        name = ugettext_lazy('Sofort payment')
        author = 'Raphael Michel'
        description = ugettext_lazy('pretix payment via Klarna Sofort')
        visible = True
        version = __version__

    def ready(self):
        from . import signals  # NOQA


default_app_config = 'pretix_sofort.PluginApp'
