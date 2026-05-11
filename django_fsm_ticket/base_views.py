# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.http import Http404
from django.shortcuts import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView
from django.views.generic.edit import FormView
from django.db import transaction
from django import forms


import traceback
import logging

from django_fsm_ticket.utils import import_path_or_string


class ActionError(Exception):
    """
    Exception class that transition can raise to cancel transition with an error

    Error will be displayed to the user
    """


ticket_update_exclude_fields = [
    "ts",
    "user",
    "seq",
    "ticket",
    "source_state",
    "target_state",
    "previous",
]


ticket_create_exclude_fields = [
    "ts",
    "user",
    "ticket",
    "state",
    "last_update",
]


# Extendable forms and views


class ActionMixin:
    def __init__(self, *args, **kwargs):
        self.base_context = None
        self.ticket = None
        self.action = None
        super().__init__(*args, **kwargs)

    def get_params(self):
        if self.base_context is None:
            self.base_context, self.ticket, self.action = self.args

    def get_success_url(self):
        self.get_params()
        return reverse(
            "single_ticket", args=[self.base_context["root_name"], self.ticket.pk]
        )

    def get_context_data(self, **kwargs):
        self.get_params()
        kwargs.update(self.base_context)
        kwargs["action"] = self.action
        kwargs["ticket"] = self.ticket
        return super().get_context_data(**kwargs)


class CreateMixin:
    def __init__(self, *args, **kwargs):
        self.base_context = None
        super().__init__(*args, **kwargs)

    def get_params(self):
        if self.base_context is None:
            self.base_context = self.args[0]

    def get_success_url(self):
        self.get_params()
        return reverse(
            "single_ticket", args=[self.base_context["root_name"], self.ticket.pk]
        )

    def get_context_data(self, **kwargs):
        self.get_params()
        kwargs.update(self.base_context)
        return super().get_context_data(**kwargs)


def ticket_action_view_factory(TicketUpdateModel):
    if TicketUpdateModel.view is not None:
        return import_path_or_string(TicketUpdateModel.view)

    if TicketUpdateModel.form is not None:
        model_form = import_path_or_string(TicketUpdateModel.form)
    else:

        class TicketUpdateForm(forms.ModelForm):
            def __init__(self, limit_querysets, ticket, *args, **kwargs):
                # ticket is passed from the view so that
                # a form can use it for validation etc
                super().__init__(*args, **kwargs)
                for field, queryset in limit_querysets.items():
                    self.fields[field].queryset = queryset

            class Meta:
                model = TicketUpdateModel
                exclude = (
                    ticket_update_exclude_fields + TicketUpdateModel.exclude_fields
                )

        model_form = TicketUpdateForm

    class ActionView(ActionMixin, FormView):
        template_name = TicketUpdateModel.action_view_template
        form_class = model_form

        @transaction.atomic()
        def form_valid(self, form):
            try:
                self.get_params()
                ticket_update = form.save(commit=False)
                ticket_update.user = self.request.user if not self.request.user.is_anonymous else None
                ticket_update.save()
                self.ticket_update = ticket_update
                form.save_m2m()
                self.action.function(self.ticket, ticket_update)
                return super().form_valid(form)
            except ActionError as ae:
                form.add_error(None, str(ae))
                transaction.set_rollback(True)
                return super().form_invalid(form)
            except Exception as e:
                logging.error(e)
                logging.error(traceback.format_exc())
                form.add_error(None, _("Sorry, an unexpected error occurred. Please try again later."))
                transaction.set_rollback(True)
                return super().form_invalid(form)

        def get_success_url(self):
            self.get_params()
            return (
                reverse(
                    "single_ticket",
                    args=[self.base_context["root_name"], self.ticket.pk],
                )
                + f"#ticketupdate-{self.ticket_update.seq}"
            )

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(TicketUpdateModel.get_custom_view_context(self.ticket))
            return ctx

        def get_form_kwargs(self):
            """Return the keyword arguments for instantiating the form."""
            kwargs = super().get_form_kwargs()
            self.get_params()
            kwargs["limit_querysets"] = TicketUpdateModel.limit_view_querysets(
                self.ticket
            )
            kwargs["ticket"] = self.ticket
            return kwargs

    return ActionView


def ticket_create_view_factory(TicketModel):
    if TicketModel.form is None:

        class TicketCreateForm(forms.ModelForm):
            class Meta:
                model = TicketModel
                exclude = ticket_create_exclude_fields + TicketModel.exclude_fields

    class CreateView(CreateMixin, FormView):
        template_name = "django_fsm_ticket/ticketcreate.html"
        form_class = (
            TicketCreateForm
            if TicketModel.form is None
            else import_path_or_string(TicketModel.form)
        )

        def form_valid(self, form):
            self.get_params()
            ticket = form.save(commit=False)
            ticket.user = self.request.user
            ticket.save()
            form.save_m2m()
            ticket.on_ticket_created()
            self.ticket = ticket
            return super().form_valid(form)

    return CreateView


class TicketListView(TemplateView):
    queryset = None

    def dispatch(self, request, *args, **kwargs):
        """
        Get ticket engine and save 'is_closed' param
        """
        from .models import engine_registry

        try:
            self.ticket_engine = engine_registry[self.kwargs.get("root_name")]
        except KeyError:
            raise Http404()
        self.show_closed = self.request.GET.get("show_closed", 0)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        """
        Get ticket queryset
        """
        if self.queryset is not None:
            return self.queryset
        return self.ticket_engine.TicketClass.prefilter_visible(self.request)

    def get_tickets_by_category(self):
        """
        Return a list of dictionaries with keys: (category, ticket_dicts)

        ticket_dicts is a list of dictionaries with keys: (ticket, actions, priority, category)
        """
        tickets = self.get_queryset()
        if not self.show_closed:
            tickets = self.ticket_engine.filter_out_closed(tickets)
        tickets = [t for t in tickets if t.is_visible(self.request)]
        cats = list(
            reversed(
                self.ticket_engine.categorize_by_priority(tickets, self.request.user)
            )
        )
        if not self.show_closed:
            cats = cats[:-1]
        return cats

    def get_ticket_context(self, **kwargs):
        return {
            "categories": self.get_tickets_by_category(),
            "show_closed": self.show_closed,
            "can_create": self.ticket_engine.create_permission(self.request.user),
        }

    def get_context_data(self, **kwargs):
        """
        Prepare context
        """
        context = super().get_context_data(**kwargs)
        context.update(self.ticket_engine.get_base_context())
        context.update(self.get_ticket_context(**kwargs))
        return context

    def get_template_names(self):
        """
        Return the template to use

        precedence is given to the view's template, after that the ticket_engine
        """
        return (
            self.template_name
            if self.template_name
            else self.ticket_engine.template_list
        )
