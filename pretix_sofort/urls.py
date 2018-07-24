from django.conf.urls import include, url
from pretix.multidomain import event_url

from .views import ReturnView, redirect_view, webhook

event_patterns = [
    url(r'^sofort/', include([
        url(r'^redirect/$', redirect_view, name='redirect'),
        url(r'^return/(?P<order>[^/]+)/(?P<hash>[^/]+)/$', ReturnView.as_view(), name='return'),
        event_url(r'^webhook/$', webhook, name='webhook', require_live=False),
    ])),
]
