# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import warnings

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
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


_UNSET = object()
api_prefix = getattr(settings, "FSM_TICKET_API_PREFIX", _UNSET)
if api_prefix is _UNSET:
    api_prefix = "api"
    warnings.warn(
        "Missing FSM_TICKET_API_PREFIX\n"
        "If you are using the API, please explicitly define FSM_TICKET_API_PREFIX = 'api'.\n"
        "If not, it is recommended to define FSM_TICKET_API_PREFIX = None\n"
        "Starting from django_fsm_ticket 2.0, the default will be None\n"
        "(i.e. API will not be active unless you explicitly define an FSM_TICKET_API_PREFIX)",
        FutureWarning,
    )
if api_prefix is None:
    api_enabled = False
elif not isinstance(api_prefix, str):
    raise ImproperlyConfigured(
        "FSM_TICKET_API_PREFIX must be a string or None, "
        f"not {type(api_prefix).__name__}"
    )
else:
    api_prefix = api_prefix.strip("/")
    if not api_prefix:
        raise ImproperlyConfigured(
            "FSM_TICKET_API_PREFIX cannot be empty: "
            "set it to None to disable the API, or to a non-empty prefix such as 'api'"
        )


if api_enabled:
    from django_fsm_ticket import api
    urlpatterns += [
        path(f"{api_prefix}/<str:root_name>", api.ticket_list_factory, name="ticket-list"),
        path(f"{api_prefix}/<str:root_name>/ticket-update", api.ticket_update_list_factory, name="ticket-update-list"),
        path(f"{api_prefix}/<str:root_name>/ticket-update/<int:pk>", api.ticket_update_detail_factory, name="ticket-update-detail"),
        path(f"{api_prefix}/<str:root_name>/<int:pk>", api.ticket_detail_factory, name="ticket-detail"),
        path(f"{api_prefix}/<str:root_name>/<int:pk>/<str:action_name>", api.ticket_action_factory, name="ticket-detail-action"),
    ]
