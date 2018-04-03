from django.conf.urls import url, include

from pretix.multidomain import event_url
from .views import webhook, redirect_view, ReturnView

urlpatterns = [
]

event_patterns = [
    url(r'^sofort/', include([
        url(r'^redirect/$', redirect_view, name='redirect'),
        url(r'^return/(?P<order>[^/]+)/(?P<hash>[^/]+)/$', ReturnView.as_view(), name='return'),
        event_url(r'^webhook/$', webhook, name='webhook', require_live=False),
    ])),
]
