# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django_filters.rest_framework import DjangoFilterBackend
from django_fsm import can_proceed, has_transition_perm
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.http import Http404
from django.utils.translation import gettext as _

from django_fsm_ticket.filters import TicketUpdateFilter, ticket_filter_factory
from django_fsm_ticket.models import Ticket, TicketUpdate, engine_registry
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

        permission_classes = [
            ticket_list_permission_factory(ticket_engine.TicketClass),
        ]
        queryset = ticket_engine.TicketClass.prefilter_visible(request)

        pagination_class = LimitOffsetPagination
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
        ordering_fields = "__all__"
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

            permission_classes = [
                IsAuthenticated,
                TicketDetailPermission,
                PrefilteredTicketVisibilityPermission,
            ]
            queryset = ticket_engine.TicketClass.prefilter_visible(request)

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
        """

        permission_classes = [IsAuthenticated]
        queryset = ticket_engine.TicketClass.prefilter_visible(request)

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
                context={"request": request, "action": action},
            )
            serializer.is_valid(raise_exception=True)
            transition = getattr(ticket, action_name)
            with transaction.atomic():
                # Create the ticket update
                ticket_update = serializer.save()
                # Call the action function
                transition(ticket_update)
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
        permission_classes = [
            ticket_list_permission_factory(ticket_engine.TicketClass),
        ]
        queryset = TicketUpdate.objects.filter(ticket__polymorphic_ctype=ctype)
        serializer_class = ticket_update_list_serializer_factory(
            TicketUpdate, ticket_engine
        )
        pagination_class = LimitOffsetPagination
        filter_backends = [
            SearchFilter,
            OrderingFilter,
            DjangoFilterBackend,
        ]
        ordering_fields = "__all__"
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
            serializer = ticket_update_serializer_class(ticket_update)
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

        permission_classes = [
            IsAuthenticated,
            TicketDetailPermission,
            TicketUpdateVisibilityPermission,
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
