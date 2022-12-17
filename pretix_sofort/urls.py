from django.urls import include, path
from pretix.multidomain import event_url

from .views import ReturnView, redirect_view, webhook

event_patterns = [
    path('sofort/', include([
        path('redirect/', redirect_view, name='redirect'),
        path('return/<str:order>/<str:hash>/', ReturnView.as_view(), name='return'),
        event_url(r'^webhook/$', webhook, name='webhook', require_live=False),
    ])),
]
