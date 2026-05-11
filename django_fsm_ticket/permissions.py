# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from rest_framework import permissions
from rest_framework.exceptions import NotFound


def ticket_list_permission_factory(ticket_model):
    class TicketListPermission(permissions.BasePermission):
        """
        Check that user has permission to create or list tickets
        """

        def has_permission(self, request, view):
            if request.method in permissions.SAFE_METHODS:
                return ticket_model.can_list(request.user)
            try:
                return ticket_model.can_create(request.user)
            except Exception:
                pass
            return False

    return TicketListPermission


class TicketDetailPermission(permissions.BasePermission):
    """
    Check that user has permission to view, update or delete ticket
    """

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return obj.is_visible(request)
        try:
            if request.method in ["PUT", "PATCH"]:
                return obj.can_modify(request.user)
            if request.method == "DELETE":
                return obj.can_delete(request.user)
        except Exception:
            pass
        return False


class TicketVisibilityPermission(permissions.BasePermission):
    """
    Check that the ticket is visible by logged user
    """
    def has_object_permission(self, request, view, obj):
        if obj.check_visibility(request):
            return True
        raise NotFound()


class PrefilteredTicketVisibilityPermission(permissions.BasePermission):
    """
    Check that the ticket (whose visibility has been prefiltered) is visible by logged user
    """
    def has_object_permission(self, request, view, obj):
        if obj.is_visible(request):
            return True
        raise NotFound()


class TicketUpdateVisibilityPermission(permissions.BasePermission):
    """
    Check that the ticket and ticket update are visible by logged user
    """
    def has_object_permission(self, request, view, obj):
        if (
            obj.ticket.check_visibility(request)
            and obj.is_visible(request)
        ):
            return True
        raise NotFound()
