# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import warnings

from django_filters.rest_framework import DjangoFilterBackend
from django_fsm import TransitionNotAllowed, can_proceed, has_transition_perm
from rest_framework import generics, status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BrowsableAPIRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.settings import api_settings

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.http import Http404
from django.utils.translation import gettext as _

from django_fsm_ticket.base_views import ActionError
from django_fsm_ticket.filters import TicketUpdateFilter, ticket_filter_factory
from django_fsm_ticket.models import Ticket, TicketUpdate, engine_registry, fsm_tickets_config
from django_fsm_ticket.permissions import (
    ticket_list_permission_factory,
    TicketDetailPermission,
    TicketUpdateVisibilityPermission,
    PrefilteredTicketVisibilityPermission,
)
from django_fsm_ticket.serializers import (
    ticket_detail_serializer_factory,
    ticket_list_serializer_factory,
    ticket_update_create_serializer_factory,
    ticket_update_list_serializer_factory,
)
from django_fsm_ticket.utils import import_path_or_string


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = _("This action is no longer available: the ticket has been modified in the meantime")
    default_code = "conflict"


# Default implementations, which do not hide anything
DEFAULT_IS_VISIBLE = (Ticket.is_visible, TicketUpdate.is_visible)


def overrides_is_visible(cls):
    """
    Tell if `cls` or one of its subclasses overrides the default `is_visible` method
    """
    if cls.is_visible not in DEFAULT_IS_VISIBLE:
        return True
    return any(overrides_is_visible(sub) for sub in cls.__subclasses__())


def paginate_visible(view, queryset, is_visible):
    """
    Return the items of `queryset` for which `is_visible(item)` is True, paginated

    Visibility is checked on the whole queryset before paginating, so that
    counts and pages do not disclose anything about non-visible items.
    Only primary keys of visible items are kept in memory.

    Return a pair (items, paginated)
    """
    pks = [item.pk for item in queryset.iterator() if is_visible(item)]
    page = view.paginate_queryset(pks)
    if page is not None:
        pks = page
    items = {item.pk: item for item in queryset.filter(pk__in=pks)}
    return [items[pk] for pk in pks], page is not None


_UNSET = object()


class TicketPagination(LimitOffsetPagination):
    """
    Limit-offset pagination, with page size bounded by `FSM_TICKET_CONFIG.API_MAX_LIMIT`

    If `API_MAX_LIMIT` is set, lists are always paginated, even if no `limit` is requested,
    and requested limits are capped. If it is `None` (or unset, but this is deprecated),
    lists are paginated only if `limit` is requested (or a DRF `PAGE_SIZE` is set), without any cap.
    """

    def get_limit(self, request):
        limit = super().get_limit(request)
        # Read at each request, since config may change at runtime (e.g. with django-constance)
        max_limit = getattr(fsm_tickets_config, "API_MAX_LIMIT", _UNSET)
        if max_limit is _UNSET:
            max_limit = None
            warnings.warn(
                "Missing API_MAX_LIMIT in FSM_TICKET_CONFIG\n"
                "Starting from django_fsm_ticket 2.0, the default will be 25: "
                "API lists will always be paginated, with at most 25 items per page.\n"
                "Set API_MAX_LIMIT to the maximum page size, or to None to keep pages unbounded.",
                FutureWarning,
            )
        if not max_limit:
            return limit
        if limit is None:
            return max_limit
        return min(limit, max_limit)



def api_renderers():
    """
    Return the renderer classes of generated API views: the browsable API is only available in DEBUG mode

    The browsable API is a development tool, and its HTML forms may enumerate related objects.
    Other configured renderers are kept; JSON is used if no renderer is left.
    """
    renderers = list(api_settings.DEFAULT_RENDERER_CLASSES)
    if not settings.DEBUG:
        renderers = [r for r in renderers if not issubclass(r, BrowsableAPIRenderer)]
    return renderers or [JSONRenderer]


def orderable_fields(model, exclude_fields):
    """
    Return the names of the fields of `model` which can be used for ordering

    Excluded fields cannot, since ordering would disclose their values
    """
    return [f.name for f in model._meta.fields if f.name not in exclude_fields]


def ticket_list_factory(request, root_name, *args, **kwargs):
    try:
        ticket_engine = engine_registry[root_name]
    except KeyError:
        raise Http404()

    class TicketListApiView(generics.ListCreateAPIView):
        """
        List view of ticket (list and create),
        returns the 'ticket_detail_api_view' set on the
        ticket engine, or a generated one. Uses the 'serizalizer' set on the
        TicketClass or a generated one (ModelSerializer)

        Will return 404 if user does not have
        view access.
        """

        renderer_classes = api_renderers()
        permission_classes = [
            ticket_list_permission_factory(ticket_engine.TicketClass),
        ]

        def get_queryset(self):
            # Use the request authenticated by DRF (not the Django request received by the factory,
            # whose user is the one of the session, if any)
            return ticket_engine.TicketClass.prefilter_visible(self.request)

        pagination_class = TicketPagination
        filter_backends = [
            SearchFilter,
            OrderingFilter,
            DjangoFilterBackend,
        ]
        filterset_class = (
            import_path_or_string(ticket_engine.TicketClass.filterset_class)
            if ticket_engine.TicketClass.filterset_class
            else ticket_filter_factory(ticket_engine.TicketClass)
        )
        ordering_fields = orderable_fields(
            ticket_engine.TicketClass,
            ["polymorphic_ctype"] + ticket_engine.TicketClass.exclude_fields,
        )
        search_fields = ticket_engine.TicketClass.search_fields
        ordering = ["-id"]

        def get_serializer_class(self):
            if self.request.method in ["POST", "PATCH", "PUT"]:
                if ticket_engine.TicketClass.write_serializer:
                    return import_path_or_string(
                        ticket_engine.TicketClass.write_serializer
                    )
                return ticket_detail_serializer_factory(ticket_engine)
            if ticket_engine.TicketClass.list_serializer:
                return import_path_or_string(ticket_engine.TicketClass.list_serializer)
            return ticket_list_serializer_factory(ticket_engine)

        def list(self, request, *args, **kwargs):
            queryset = self.filter_queryset(self.get_queryset())

            if overrides_is_visible(ticket_engine.TicketClass):
                # prefilter_visible may not be exact
                visible_tickets, paginated = paginate_visible(
                    self, queryset, lambda ticket: ticket.is_visible(request)
                )
            else:
                page = self.paginate_queryset(queryset)
                paginated = page is not None
                visible_tickets = page if paginated else list(queryset)

            serializer = self.get_serializer(visible_tickets, many=True)
            if paginated:
                return self.get_paginated_response(serializer.data)

            return Response(serializer.data)

        def perform_create(self, serializer):
            with transaction.atomic():
                ticket = serializer.save()
                ticket.user = self.request.user
                ticket.save()
            ticket.on_ticket_created()

    view = (
        ticket_engine.TicketClass.list_api_view
        if ticket_engine.TicketClass.list_api_view
        else TicketListApiView
    )
    return view.as_view()(request, *args, **kwargs)


def ticket_detail_factory(request, root_name, *args, **kwargs):
    try:
        ticket_engine = engine_registry[root_name]
    except KeyError:
        raise Http404()

    if ticket_engine.TicketClass.detail_api_view:
        view = import_path_or_string(ticket_engine.TicketClass.detail_api_view)
    else:

        class TicketDetailApiView(generics.RetrieveUpdateDestroyAPIView):
            """
            Detail view of ticket (retrieve, update and destroy),
            returns the 'ticket_detail_api_view' set on the
            ticket engine, or a generated one. Uses the 'serizalizer' set on the
            TicketClass or a generated one (ModelSerializer)

            Will return 404 if user does not have
            view access.
            """

            renderer_classes = api_renderers()
            permission_classes = [
                IsAuthenticated,
                # Visibility first: non-visible tickets are not found (404), rather than forbidden (403)
                PrefilteredTicketVisibilityPermission,
                TicketDetailPermission,
            ]

            def get_queryset(self):
                # Use the request authenticated by DRF (not the Django request received by the factory,
                # whose user is the one of the session, if any)
                return ticket_engine.TicketClass.prefilter_visible(self.request)

            def get_serializer_class(self):
                if self.request.method in ["POST", "PATCH", "PUT"]:
                    if ticket_engine.TicketClass.write_serializer:
                        return import_path_or_string(
                            ticket_engine.TicketClass.write_serializer
                        )
                    return ticket_detail_serializer_factory(ticket_engine)
                if ticket_engine.TicketClass.detail_serializer:
                    return import_path_or_string(
                        ticket_engine.TicketClass.detail_serializer
                    )
                return ticket_detail_serializer_factory(ticket_engine)

            def perform_update(self, serializer):
                """
                Save the ticket without overwriting the outcome of a concurrent action

                The whole ticket is saved: lock it and reload its state, as actions do, then check
                permissions again, since they may depend on the state.
                """
                with transaction.atomic():
                    ticket = serializer.instance
                    ticket._lock_for_transition()
                    self.check_object_permissions(self.request, ticket)
                    serializer.save()

        view = TicketDetailApiView
    return view.as_view()(request, *args, **kwargs)


def ticket_action_factory(request, root_name, action_name, *args, **kwargs):
    try:
        ticket_engine = engine_registry[root_name]
        action = ticket_engine.actions[action_name]
    except KeyError:
        raise Http404()

    class TicketActionApiView(generics.UpdateAPIView):
        """
        The view for an action on a ticket,
        applies the action (from url) to the ticket

        Checks if the action can be applied in has_transition_permission

        Will return 404 if user does not have
        view access.
        """

        renderer_classes = api_renderers()
        permission_classes = [
            IsAuthenticated,
            PrefilteredTicketVisibilityPermission,
        ]

        def get_queryset(self):
            # Use the request authenticated by DRF (not the Django request received by the factory,
            # whose user is the one of the session, if any)
            return ticket_engine.TicketClass.prefilter_visible(self.request)

        def get_serializer_class(self):
            if action.ticket_update_model.write_serializer:
                return import_path_or_string(
                    action.ticket_update_model.write_serializer
                )
            return ticket_update_create_serializer_factory(action.ticket_update_model)

        def update(self, request, *args, **kwargs):
            ticket = self.get_object()
            transition = getattr(ticket, action_name)
            if not can_proceed(transition) or not has_transition_perm(
                transition, request.user
            ):
                raise PermissionDenied(_("Action {} not permitted").format(action_name))
            data = request.data.copy()
            data["ticket"] = ticket.id
            serializer_class = self.get_serializer_class()
            serializer = serializer_class(
                data=data,
                context={"request": request, "action": action, "ticket": ticket},
            )
            serializer.is_valid(raise_exception=True)
            transition = getattr(ticket, action_name)
            with transaction.atomic():
                # Create the ticket update
                ticket_update = serializer.save()
                # Call the action function; errors raised by the action are shown, as in HTML views
                try:
                    transition(ticket_update)
                except TransitionNotAllowed:
                    # A concurrent action changed the state of the ticket
                    raise Conflict()
                except ActionError as e:
                    raise ValidationError({"non_field_errors": [str(e)]})
                ticket.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)

    return TicketActionApiView.as_view()(request, *args, **kwargs)


def ticket_update_list_factory(request, root_name, *args, **kwargs):
    """
    If ticket class has 'ticket_update_list_api_view', then returns that view,
    otherwise returns a view listing all TicketUpdates for the
    specific TicketClass
    """

    try:
        ticket_engine = engine_registry[root_name]
        ctype = ContentType.objects.get_for_model(ticket_engine.TicketClass)
    except KeyError:
        raise Http404()

    class TicketUpdateListApiView(generics.ListAPIView):
        """
        List all ticket updates for this ticket engine

        Only ticket updates visible to current user are shown:
        - ticket must be visible
        - ticket update must be visible

        Notice that tickets are prefiltered for visibility when loading the queryset.
        If `is_visible()` is overridden, either by the ticket class or by any ticket update class,
        we need to check, for each ticket update in the queryset:
        - if the relative ticket is_visible()
        - if the ticket update itself is_visible()

        This check is done on the whole queryset before paginating, so that counts and pages
        do not disclose anything about non-visible ticket updates; this has a cost which is
        linear in the number of ticket updates in the queryset (each ticket is loaded only once).
        """
        renderer_classes = api_renderers()
        permission_classes = [
            ticket_list_permission_factory(ticket_engine.TicketClass),
        ]
        queryset = TicketUpdate.objects.filter(ticket__polymorphic_ctype=ctype)
        serializer_class = ticket_update_list_serializer_factory(
            TicketUpdate, ticket_engine
        )
        pagination_class = TicketPagination
        filter_backends = [
            SearchFilter,
            OrderingFilter,
            DjangoFilterBackend,
        ]
        ordering_fields = orderable_fields(
            TicketUpdate, ["polymorphic_ctype"] + TicketUpdate.exclude_fields
        )
        search_fields = ["notes"]
        filterset_class = TicketUpdateFilter
        ordering = ["-id"]

        def get_queryset(self):
            qs = super().get_queryset()
            return qs.filter(
                ticket__in=ticket_engine.TicketClass.prefilter_visible(self.request),
            )

        def serialize_ticket_update(self, ticket_update):
            if ticket_update.list_serializer:
                ticket_update_serializer_class = import_path_or_string(
                    ticket_update.list_serializer
                )
            else:
                ticket_update_serializer_class = ticket_update_list_serializer_factory(
                    ticket_update.__class__, ticket_engine
                )
            serializer = ticket_update_serializer_class(
                ticket_update, context=self.get_serializer_context()
            )
            return serializer.data

        def list(self, request, *args, **kwargs):
            """Override to have separate serializer for each update"""
            queryset = self.filter_queryset(self.get_queryset())

            check_tickets = overrides_is_visible(ticket_engine.TicketClass)
            if check_tickets or overrides_is_visible(TicketUpdate):
                # Load each ticket and check its visibility only once
                tickets = {}

                def is_visible(tu):
                    if tu.ticket_id not in tickets:
                        ticket = tu.ticket
                        tickets[tu.ticket_id] = (
                            ticket,
                            not check_tickets or ticket.is_visible(request),
                        )
                    ticket, ticket_visible = tickets[tu.ticket_id]
                    if not ticket_visible:
                        return False
                    tu.ticket = ticket
                    return tu.is_visible(request)

                ticket_updates, paginated = paginate_visible(self, queryset, is_visible)
            else:
                page = self.paginate_queryset(queryset)
                paginated = page is not None
                ticket_updates = page if paginated else list(queryset)

            data = [self.serialize_ticket_update(tu) for tu in ticket_updates]

            if paginated:
                return self.get_paginated_response(data)

            return Response(data)

    view = (
        ticket_engine.TicketClass.ticket_update_list_api_view
        if ticket_engine.TicketClass.ticket_update_list_api_view
        else TicketUpdateListApiView
    )
    return view.as_view()(request, *args, **kwargs)


def ticket_update_detail_factory(request, root_name, *args, **kwargs):
    try:
        ticket_engine = engine_registry[root_name]
        ctype = ContentType.objects.get_for_model(ticket_engine.TicketClass)
    except KeyError:
        raise Http404()

    class TicketUpdateDetailApiView(generics.RetrieveAPIView):
        """
        Detail view of ticket update (read only),
        returns the 'ticket_detail_api_view' set on the
        ticket engine, or a generated one. Uses the 'serizalizer' set on the
        TicketClass or a generated one (ModelSerializer)

        Will return 404 if user does not have
        view access.
        """

        renderer_classes = api_renderers()
        permission_classes = [
            IsAuthenticated,
            # Visibility first: non-visible ticket updates are not found (404), rather than forbidden (403)
            TicketUpdateVisibilityPermission,
            TicketDetailPermission,
        ]
        queryset = TicketUpdate.objects.filter(ticket__polymorphic_ctype=ctype)
        serializer_class = ticket_update_list_serializer_factory(
            TicketUpdate, ticket_engine
        )

        def get_serializer(self, obj, *args, **kwargs):
            """
            Return the serializer instance that should be used for validating and
            deserializing input, and for serializing output.
            """
            if obj.__class__.detail_serializer:
                serializer_class = import_path_or_string(
                    obj.__class__.detail_serializer
                )
            else:
                serializer_class = ticket_update_list_serializer_factory(
                    obj.__class__, ticket_engine
                )
            kwargs.setdefault("context", self.get_serializer_context())
            return serializer_class(obj, *args, **kwargs)

        def retrieve(self, request, *args, **kwargs):
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            return Response(serializer.data)

    return TicketUpdateDetailApiView.as_view()(request, *args, **kwargs)
