# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.conf import settings
from django.urls import path

from . import views


urlpatterns = [
    path("download_attachment/<int:id>/<str:uuid>", views.download_attachment, name="ticket_download_attachment"),
    path("delete_attachment/<int:id>/<str:uuid>", views.delete_attachment, name="ticket_delete_attachment"),
    path("<str:root_name>", views.all_tickets, name="all_tickets"),
    path("<str:root_name>/create", views.create_ticket, name="create_ticket"),
    path("<str:root_name>/<int:pk>", views.single_ticket, name="single_ticket"),
    path("<str:root_name>/<int:pk>/<str:action_name>", views.ticket_action, name="ticket_action"),
]

# REST API urls
try:
    import rest_framework  # noqa: F401
    import django_filters  # noqa: F401
    api_enabled = True
except ImportError:
    api_enabled = False

if api_enabled:
    from django_fsm_ticket import api
    api_prefix = getattr(settings, "FSM_TICKET_API_PREFIX", "api")

    urlpatterns += [
        path(f"{api_prefix}/<str:root_name>", api.ticket_list_factory, name="ticket-list"),
        path(f"{api_prefix}/<str:root_name>/ticket-update", api.ticket_update_list_factory, name="ticket-update-list"),
        path(f"{api_prefix}/<str:root_name>/ticket-update/<int:pk>", api.ticket_update_detail_factory, name="ticket-update-detail"),
        path(f"{api_prefix}/<str:root_name>/<int:pk>", api.ticket_detail_factory, name="ticket-detail"),
        path(f"{api_prefix}/<str:root_name>/<int:pk>/<str:action_name>", api.ticket_action_factory, name="ticket-detail-action"),
    ]
