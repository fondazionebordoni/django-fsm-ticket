# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.views.decorators.csrf import csrf_protect
from django_fsm import TransitionNotAllowed, can_proceed, has_transition_perm

from django.contrib.auth.views import redirect_to_login
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import Http404, redirect, reverse
from django.views.decorators.http import require_POST, require_safe

from .models import (
    engine_registry,
    Ticket,
    AttachmentTicketUpdate,
    DeleteAttachmentTicketUpdate,
)

from .utils import serve_modelfile
from .decorators import ticketupdate_visible_to_user
from .base_views import TicketListView  # noqa exposed to external clients

@require_safe
def all_tickets(request, root_name):
    try:
        ticket_engine = engine_registry[root_name]
    except KeyError:
        raise Http404()
    if not ticket_engine.TicketClass.can_list(request.user):
        return redirect_to_login(request.path)
    view = ticket_engine.ticket_list_view
    return view.as_view()(request, root_name=root_name)


@require_safe
def single_ticket(request, root_name, pk):
    try:
        ticket_engine = engine_registry[root_name]
        ticket = ticket_engine.TicketClass.prefilter_visible(request).get(pk=pk)
    except KeyError:
        return redirect_to_login(request.path)
    except ticket_engine.TicketClass.DoesNotExist:
        return redirect_to_login(request.path)
    if not ticket.is_visible(request):
        return redirect_to_login(request.path)
    actions = ticket_engine.get_ticket_actions(ticket, request.user)
    ctx = ticket_engine.get_base_context()
    ctx["actions"] = actions
    ctx["request"] = request
    ctx["user"] = request.user
    return HttpResponse(ticket.render(request, ctx))


@csrf_protect
def ticket_action(request, root_name, pk, action_name):
    try:
        ticket_engine = engine_registry[root_name]
        action = ticket_engine.actions[action_name]
        ticket = ticket_engine.TicketClass.prefilter_visible(request).get(pk=pk)
    except KeyError:
        raise Http404
    except Ticket.DoesNotExist:
        raise Http404
    if not ticket.is_visible(request):
        raise Http404
    transition = getattr(ticket, action_name)
    if not can_proceed(transition):
        raise Http404
    if not has_transition_perm(transition, request.user):
        return HttpResponseForbidden()
    base_context = ticket_engine.get_base_context()
    return action.view(request, base_context, ticket, action)


@csrf_protect
def create_ticket(request, root_name):
    try:
        ticket_engine = engine_registry[root_name]
    except KeyError:
        raise Http404()
    base_context = ticket_engine.get_base_context()
    return ticket_engine.create_ticket_view(request, base_context)


@ticketupdate_visible_to_user("id")
@require_safe
def download_attachment(request, id, uuid):
    try:
        atu = AttachmentTicketUpdate.objects.get(id=id, uuid=uuid)
    except AttachmentTicketUpdate.DoesNotExist:
        raise Http404()
    if atu.deleted and not request.user.is_superuser:
        raise Http404()
    if not atu.file:
        raise Http404()
    return serve_modelfile(atu.file, atu.name)


@ticketupdate_visible_to_user("id")
@require_POST
@transaction.atomic
def delete_attachment(request, id, uuid):
    """
    Mark an attachment as deleted, through the `delete_attachment` transition of its ticket

    Nothing is written unless the transition is allowed in current state and to current user
    """
    try:
        atu = AttachmentTicketUpdate.objects.select_for_update().get(id=id, uuid=uuid, deleted=False)
    except AttachmentTicketUpdate.DoesNotExist:
        raise Http404()
    t = atu.ticket
    if not t.can_delete_attachment(request):
        return redirect_to_login(request.path)
    transition = getattr(t, "delete_attachment", None)
    if transition is None or not can_proceed(transition):
        raise Http404()
    if not has_transition_perm(transition, request.user):
        return HttpResponseForbidden()
    atu.deleted = True
    atu.save()
    try:
        transition(DeleteAttachmentTicketUpdate(attachment=atu, user=request.user))
    except TransitionNotAllowed:
        # A concurrent action changed the state of the ticket: the deletion is rolled back
        raise Http404()
    return redirect(reverse("single_ticket", args=(t.root_name, t.pk)))
