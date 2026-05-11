# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404

from .models import Ticket, TicketUpdate
from .utils import decorator_factory


@decorator_factory
def ticket_visible_to_user(ticket_pk_param_name='ticket_pk'):
    """
    Decorator factory: execute view `f` if it pertains a ticket visible to current user

    View `f` shall take a parameter named `ticket_pk_param_name`
    """
    def decorator(f):
        @wraps(f)
        def g(request, *args, **kwargs):
            try:
                ticket = Ticket.objects.get(pk=kwargs[ticket_pk_param_name])
            except KeyError:
                raise ImproperlyConfigured(
                    f"Function {f.__name__} did not receive a required parameter named {ticket_pk_param_name}"
                )
            except Ticket.DoesNotExist:
                raise Http404()
            if not ticket.check_visibility(request):
                return redirect_to_login(request.path)
            return f(request, *args, **kwargs)
        return g
    return decorator


@decorator_factory
def ticketupdate_visible_to_user(ticketupdate_pk_param_name='ticketupdate_pk'):
    """
    Decorator: execute view `f` if it pertains a ticket update visible to current user

    View `f` shall take a parameter named `ticketupdate_pk_param_name`
    """
    def decorator(f):
        @wraps(f)
        def g(request, *args, **kwargs):
            try:
                tu = TicketUpdate.objects.get(pk=kwargs[ticketupdate_pk_param_name])
            except KeyError:
                raise ImproperlyConfigured(
                    f"Function {f.__name__} did not receive a required parameter named {ticketupdate_pk_param_name}"
                )
            except TicketUpdate.DoesNotExist:
                raise Http404()
            if not tu.ticket.check_visibility(request) or not tu.is_visible(request):
                return redirect_to_login(request.path)
            return f(request, *args, **kwargs)
        return g
    return decorator
