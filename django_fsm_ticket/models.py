# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from collections import deque
from functools import wraps
import logging
from typing import Iterable
from urllib.parse import urljoin
import uuid

from django_fsm import FSMField, transition as fsm_transition
from polymorphic.managers import PolymorphicManager
from polymorphic.models import PolymorphicModel
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.views import redirect_to_login
from django.core.mail import send_mail
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from django.shortcuts import reverse
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .base_views import TicketListView, ticket_action_view_factory, ticket_create_view_factory
from .utils import ThresholdMap
from . import utils

User = get_user_model()
transition = fsm_transition

# Map TicketEngine root_names to instances.
# Automatically updated by TicketEngine.__init__ method
engine_registry = {}

try:
    fsm_tickets_config = settings.FSM_TICKET_CONFIG
except AttributeError:
    fsm_tickets_config = object()


def user_to_mail_recipient(user):
    """
    Returns an email recipient string from a user instance
    """
    return f"{user} <{user.email}>"


class Action:
    def __init__(self, verbose_name, ticket_update_model=None, priority=0.5, function=None):
        """
        An instance of this class abstracts an action that can be done on a ticket

        @param verbose_name: name for the action that will be displayed, e.g. on buttons
        @param ticket_update_model: a model derived from `TicketUpdate`. If `None`, the
            action will generate a `TicketUpdate` instance.
        @param priority: tickets with higher (larger) priority actions will be shown first.
            If `priority` is lower than 0.5, the action button will be outlined
            If `priority` is 0, the action button will not appear
        @param function: transition function (unbound method)
        """
        self.name = function.__name__
        self.verbose_name = verbose_name if verbose_name is not None else self.name.capitalize()
        self.ticket_update_model = TicketUpdate if ticket_update_model is None else ticket_update_model
        self.view = ticket_action_view_factory(self.ticket_update_model)
        if hasattr(self.view, 'as_view'):
            self.view = self.view.as_view()
        self.priority = priority
        self.function = function


def after_transition(f):
    """
    Decorator to cause f to be called after completion of current transition

    This mechanism allows an action to call another action: the second action
    is not immediately executed (otherwise the first state transition would not
    be complete at the time when second transition is invoked). Instead,
    the @after_transition decorator puts the second action in a queue, so that
    it will be executed after the first transition is done.
    """
    @wraps(f)
    def g(self, *args, **kwargs):
        self._actions_queue.appendleft((f, args, kwargs))
        self._process_actions()
    return g


def filter_notification_users(users, notification_level=None):
    """
    Filter users with respect to their notification group and notification level

    @param notification_level: override config-defined notification level
    """
    if len(users) == 0:
        return users
    if notification_level is not None:
        lev = notification_level
    else:
        try:
            lev = str(fsm_tickets_config.NOTIFICATION_LEVEL)
        except AttributeError:
            lev = '2'
    if lev == '0':
        return []
    if lev == '1':
        return User.objects.filter(pk__in=(u.pk for u in users), groups__name='notifications_always')
    if lev == '2':
        return User.objects.filter(pk__in=(u.pk for u in users)).exclude(groups__name='notifications_never')
    if lev == '3':
        return users
    raise ImproperlyConfigured(f"Not a valid NOTIFICATION_LEVEL: {lev}")


class FakeRequest:
    def __init__(self, user):
        self.user = user


class UUIDNaturalKeyPolymorphicManager(PolymorphicManager):
    def get_by_natural_key(self, uuid):
        return self.get(uuid=uuid)


class UUIDNaturalKeyPolymorphicMixin(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    objects = UUIDNaturalKeyPolymorphicManager()

    class Meta:
        abstract = True

    def natural_key(self):
        return (str(self.uuid),)


class Ticket(UUIDNaturalKeyPolymorphicMixin, PolymorphicModel):
    """
    - `template_fields`: template for ticket fields
    - `template`: template for the whole single ticket (includes template_fields)
    - `notification_template`: template for textual mail notifications. First line is for
      email subject
    - `notification_from`: mail sender address
    - `create_view` an alternative django view to create tickets
    - `list_view` an alternative django view to list tickets
    - `list_api_view`: an optional alternative API view for listing tickets
    - `detail_api_view`: an optional alternative for retrieving, updating and deleting tickets
    - `ticket_update_list_api_view`: an optional alternative API view for listing ticket updates
    - `list_serializer`: an optional alternative serializer for list views
    - `detail_serializer`: an optional alternative serializer for detail views
    - `write_serializer`: an optional alternative serializer for write views
    - `url_prefix`: property, return HTTP(S) scheme and domain of ticketing system
    - `root_name`: Name for engine instance, which will be used in URLS. In your project
      you may have several independent ticketing systems, for different workflows.
      Each ticketing system must have a different subclass of Ticket, with a different
      root_name
    - `exclude_fields`: specifies fields to be excluded from input ModelForm. Ordinarily,
      only the `title` and `description` fields are shown; they can be excluded,
      as well as additional fields introduced in subclasses
    - `search_fields`: which fields are searchable in the list API
    - `form`: if not `None`, replace automatically generated `ModelForm` with a custom
      form class. Custom form shall work like a `ModelForm`, implementing a method
      `save(commit=True)` which returns the newly created `Ticket` instance.

    For very advanced customization, if you want to replace the view that creates
    a new ticket, set the `create_view` parameter of `TicketEngine.__init__(...)`
    method.
    """
    # Basic customization
    template_fields = 'django_fsm_ticket/partials/ticket_content_fields.html'
    notification_template = 'django_fsm_ticket/notification.txt'
    reminder_template = 'django_fsm_ticket/reminder.txt'
    notification_from = None
    exclude_fields = []
    search_fields = ["title", "description"]
    create_view = None
    list_view = TicketListView
    list_api_view = None
    detail_api_view = None
    ticket_update_list_api_view = None
    list_serializer = None
    detail_serializer = None
    write_serializer = None
    filterset_class = None
    root_name = 'ticket'

    verbose_states = {
        'new': _('New'),
        'closed': _('Closed'),
    }

    closed_states = {'closed'}

    show_attachment_list = True

    # Advanced customization
    template = 'django_fsm_ticket/ticket.html'
    form = None  # If None, use an automatically created ModelForm

    # Exposed fields
    title = models.CharField(max_length=1023, verbose_name=_('Title'))
    description = models.TextField(blank=True, default='', verbose_name=_('Description'))

    # Internal (automatically managed) fields
    ts = models.DateTimeField(default=timezone.now)
    user = models.ForeignKey(User, null=True, blank=True, default=None, on_delete=models.SET_NULL)
    state = FSMField(default='new', verbose_name=_('State'))
    last_update = models.ForeignKey('TicketUpdate', null=True, blank=True, on_delete=models.SET_NULL, related_name='last_update_ticket')

    # Methods meant for overriding

    @classmethod
    def can_create(cls, user):
        """
        Returns True if `user` has permission to create a ticket of this class
        """
        return user.is_authenticated

    @classmethod
    def can_list(cls, user):
        """
        Returns True if `user` has permission to list tickets of this class

        This affects the possibility to load the list view as a whole. Visibility of single ticket instances
        (both in details and list views) is controlled by `prefilter_visible` and `is_visible` methods.
        """
        return user.is_authenticated

    def can_delete(self, user):
        """
        Returns True if `user` has permission to delete this ticket

        Most of the time, tickets should not be deleted
        """
        return False

    def can_modify(self, user):
        """
        Returns True if `user` has permission to modify the fields of this ticket
        """
        return False

    @classmethod
    def prefilter_visible(cls, request):
        """
        Return a queryset of tickets that may be visible to current user

        This method exists for performance, to offload as much as possible
        filtering of visible tickets to the database.

        Further per-ticket filtering can be done by the is_visible method
        """
        return cls.objects.all()

    def is_visible(self, request):
        """
        Returns True if this ticket is visible by current user

        Hint: use prefilter_visible to discard as many non-visible tickets
        as possible, using a single database query
        """
        return True

    def on_ticket_created(self):
        """
        Override if some action has to be performed after ticket creation

        This method shall be explicitly called by creation view,
        after ticket creation
        """

    def can_delete_attachment(self, request):
        """
        Determine if current user can delete attachments.

        Used if attachment management is enabled.
        """
        return False

    # Advanced overriding is possible

    def send_notification(self, subject, message, recipient_user):
        """
        Actually send notification.

        This method is called by notify().
        It can be overridden, e.g. to send notifications via Celery
        """
        recipient = user_to_mail_recipient(recipient_user)
        send_mail(
            subject,
            message,
            self.notification_from,
            [recipient],
        )

    def render(self, request, ctx):
        ctx.update({
            'ticket': self,
            'updates': [tu for tu in self.get_all_updates() if tu.is_visible(request)],
        })
        if self.show_attachment_list:
            ats = AttachmentTicketUpdate.objects.filter(
                ticket=self,
                deleted=False,
            ).exclude(file='')
            ctx['attachments'] = [a for a in ats if a.is_visible(request)]
            ctx['can_delete'] = self.can_delete_attachment(request)

        return render_to_string(self.template, ctx, request)

    @property
    def url_prefix(self):
        return getattr(fsm_tickets_config, "URL_PREFIX", "http://localhost")

    def __str__(self):
        return f"Ticket #{self.pk}: {self.title}"

    # API Methods
    def check_visibility(self, request):
        """
        Tell if user can see this ticket
        """
        if not self.is_visible(request):
            return False
        return self.prefilter_visible(request).filter(pk=self.pk).count() == 1

    def get_all_updates(self):
        return self.ticketupdate_set.all().order_by('ts')

    def is_closed(self):
        return self.state in self.closed_states

    def get_users_with_actions(self, min_priority=0.5):
        """
        Return all the users which may take an action on curren ticket

        Only consider action with prioririty at least `min_priority`
        """
        ticket_engine = engine_registry[self.root_name]
        us = User.objects.all()
        out = []
        for u in us:
            if self.check_visibility(FakeRequest(u)):
                actions = ticket_engine.get_ticket_actions(self, u)
                if len(actions) > 0 and actions[0].priority >= min_priority:
                    out.append(u)
        return out

    @after_transition
    def notify(
        self,
        ticket_update,
        users=None,
        min_priority=0.5,
        reminder=False,
        additional_users=None,
        notification_level=None,
        notification_template=None,
    ):
        """
        Notify users about ticket update

        If called without `users` argument, send notification automatically
        to all users who may take an action on the ticket
        after current transaction, with priority at least `min_priority`,
        excluding the user who took the action, i.e. ticket_update.user.

        Furthermore, notification is set to additional_users (if set),
        even to ticket_update.user if they belong to `additional_users`.

        Config-defined notification level can be overridden using the
        `notification_level` parameter.

        A template can be specified by passing it in the parameter
        `notification_template`, otherwise the ticket class' 
        `notification_template` or `reminder_template` will be be used.
        """
        if users is None:
            users = self.get_users_with_actions(min_priority)

        if ticket_update.user is not None:
            users = [u for u in users if u.pk != ticket_update.user.pk]

        if additional_users is not None:
            users.extend(additional_users)
            users = set(users)

        logging.info(f"Reminder about ticket #{ticket_update.ticket.pk} should be sent to: {users}")
        users = filter_notification_users(users, notification_level)
        logging.info(f"Actually sending reminder to: {users}")

        template = notification_template
        if template is None:
            template = self.reminder_template if reminder else self.notification_template

        for user in users:
            lines = render_to_string(
                template,
                {
                    'ticket': self,
                    'user': user,
                    'ticketupdate': ticket_update,
                }
            ).splitlines()
            subject = lines[0]
            msg = "\n".join(lines[1:])
            self.send_notification(subject, msg, user)

    @classmethod
    def get_all_open_tickets(cls):
        """
        Return the queryset of all the open tickets
        """
        return cls.objects.all().exclude(state__in=cls.closed_states)

    @classmethod
    def send_reminders(cls, min_priority=0.5, ticket_filter=None, notification_level=None):
        """
        Send a reminder notification to all users that shall perform an action on a ticket

        @param min_priority: only consider actions of priority at leas min_priority
        @param ticket_filter: optional function that applies a filter to the queryset
         of open tickets. The function gets a queryset of open tickets, and shall return
         an iterable of tickets.
        @param notification_level: override config-defined notification level
        """
        ts: Iterable[Ticket]
        ts = cls.get_all_open_tickets().select_related('last_update')
        if ticket_filter:
            ts = ticket_filter(ts)
        for t in ts:
            tu = t.last_update
            if tu is not None:
                t.notify(
                    tu,
                    min_priority=min_priority,
                    reminder=True,
                    notification_level=notification_level
                )

    def full_url(self):
        return urljoin(self.url_prefix, reverse('single_ticket', args=[self.root_name, self.pk]))

    def state_verbose(self, state=None):
        """
        Return verbose form for state or self.state
        """
        if state is None:
            state = self.state
        res = self.verbose_states.get(state, state)
        return res

    def user_verbose(self):
        """
        Return user as first and last name, if defined, otherwise as username
        """
        return utils.user_verbose(self.user)

    # Other methods

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._actions_queue = deque()
        self._processing_actions = False

    def _process_actions(self):
        """
        See documentation of @serialize
        """
        if self._processing_actions:
            return
        self._processing_actions = True
        try:
            while len(self._actions_queue) > 0:
                action, args, kwargs = self._actions_queue.pop()
                action(self, *args, **kwargs)
        finally:
            self._processing_actions = False

    @transaction.atomic
    def _add_update(self, tu: 'TicketUpdate'):
        """
        Adds a ticket update. Private method automatically called by transitions.

        tu is a new instance of TicketUpdate, without general fields set
        (`seq`, `ticket`, `previous`). This method will set general fields
        and save the update.
        """
        lu = self.last_update
        tu.previous = lu
        tu.ticket = self
        tu.seq = 1 if lu is None else lu.seq + 1
        tu.save()
        self.last_update = tu
        self.save()
        tu.target_state = self.state
        tu.save()


@utils.decorator_factory
def action(
    ticket_update_model=None,
    priority=0.5,
    verbose_name=None,
):
    def decorator(f):
        @after_transition
        @wraps(f)
        def g(self, ticket_update=None, *gargs, **gkwargs):
            source_state = self.state
            if ticket_update is None:
                ticket_update = ticket_update_model() if ticket_update_model is not None else TicketUpdate()
            ret = f(self, ticket_update, *gargs, **gkwargs)
            if ret is not None:
                ticket_update = ret
            ticket_update.source_state = source_state
            self._add_update(ticket_update)
            return ret

        g.action = Action(
            verbose_name=verbose_name,
            ticket_update_model=ticket_update_model,
            priority=priority,
            function=g,
        )
        return g

    return decorator


class TicketUpdate(UUIDNaturalKeyPolymorphicMixin, PolymorphicModel):
    """
    Represents a ticket update

    You can subclass this class for custom ticket updates,
    where each transition can generate a relevant (different) ticket update.

    In order to customize how the ticket appears in the ticket history,
    you may modify:
    - the `template_fields` variable (contains the snippet that show ticket update
      fields), or
    - the `template` variable (define a template that generates the whole <div>
      corresponding to the ticket update: the context contains a `ticketupdate`
      variable);
    - the `render` method, e.g. to modify the context passed to the template
    - the `notification_template` variable, for the snippet in the notification email
      with details about what happened in current update

    To customize the creation of an update, you can modify:
    - `exclude_fields`: specifies fields to be excluded from input ModelForm. Ordinarily,
      only the `notes` field is shown; it can be excluded, as well as additional fields
      introduced in subclasses can be excluded
    - `form` if not `None`, replace automatically generated `ModelForm` with a custom
      form class. Custom form shall work like a `ModelForm`, implementing a method
      `save(commit=True)` which returns the newly created `TicketUpdate` instance.
    - the whole creation view. For this aim, use `TicketEngine.set_view(...)` method.
    - `list_serializer`: an optional alternative serializer for list views
    - `detail_serializer`: an optional alternative serializer for detail views
    - `write_serializer`: an optional alternative serializer for write views
    """
    # Basic customization
    template_fields = 'django_fsm_ticket/partials/ticketupdate_fields.html'
    notification_template = 'django_fsm_ticket/notificationupdate.txt'
    exclude_fields = []
    write_serializer = None
    list_serializer = None
    detail_serializer = None

    # Advanced customization
    template = 'django_fsm_ticket/partials/ticketupdate.html'
    action_view_template = 'django_fsm_ticket/action.html'
    # view shall take parameters: (request, base_context, ticket, action)
    view = None  # If None, use an automatically created view
    form = None  # If None, use an automatically created ModelForm

    # UI fields
    notes = models.TextField(blank=True, default='', verbose_name=_('Notes'))

    # Internal fields
    ts = models.DateTimeField(default=timezone.now, db_index=True)
    user = models.ForeignKey(User, null=True, blank=True, default=None, on_delete=models.SET_NULL)
    seq = models.IntegerField(null=True)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, null=True)
    source_state = models.CharField(max_length=50, null=True)
    target_state = models.CharField(max_length=50, null=True)
    previous = models.ForeignKey('TicketUpdate', null=True, blank=True, on_delete=models.SET_NULL)

    # Methods meant for overriding

    def is_visible(self, request):
        """
        Returns True if this ticket update is visible by current user
        """
        return True

    @classmethod
    def get_custom_view_context(cls, ticket):
        """
        Return custom context to be passed by ActionView to the view template
        """
        return {}

    @classmethod
    def limit_view_querysets(cls, ticket):
        """
        For ForeignKey fields, limit queryset to be shown in ActionView

        Shall return a dictionary mapping field names to querysets,
        only for fields for which queryset shall be limited
        """
        return {}

    def render(self, ctx=None, request=None):
        if ctx is None:
            ctx = {}
        ctx.update({
            'ticket': self.ticket,
            'ticketupdate': self,
            'request': request,
        })
        return render_to_string(self.template, ctx)

    # API methods

    def source_state_verbose(self):
        return self.ticket.state_verbose(self.source_state)

    def target_state_verbose(self):
        return self.ticket.state_verbose(self.target_state)

    def user_verbose(self):
        """
        Return user as first and last name, if defined, otherwise as username
        """
        return utils.user_verbose(self.user)

    def __str__(self):
        if self.ticket is not None:
            return f"#{self.ticket.pk}.{self.seq}: {self.notes[:70]}"
        return f"No ticket: {self.notes[:70]}"


class AttachmentTicketUpdate(TicketUpdate):
    """
    Generic TicketUpdate that manages one attachment.

    The attachment may be later deleted, thus we offer a `delete` field which defaults to False.
    The `name` field may contain the original name of the file, or a verbose name: it should be either enabled
    (by removing it from excluded_field) to be manually entered by the user, or set programmatically.

    Here is an example action:

    ```python
    @action(
        verbose_name='Attach a file',
        ticket_update_model=AttachmentTicketUpdate,
    )
    @transition('state', 'new', 'new', permission=can_attach)
    @transition('state', 'rejected', 'rejected', permission=can_attach)
    def attach(self, ticket_update=None):
        if ticket_update.file:
            ticket_update.name = ticket_update.file.name
        return ticket_update
    ```
    """
    template_fields = 'django_fsm_ticket/partials/attachment_tu_fields.html'
    exclude_fields = ['uuid', 'name', 'deleted']

    # uuid = models.UUIDField(default=uuid.uuid4, null=False, unique=True, editable=False, db_index=True)
    name = models.CharField(null=True, max_length=255)
    file = models.FileField(null=True, blank=True, verbose_name=_("Attachment"))
    deleted = models.BooleanField(default=False)

    def get_name(self):
        if self.file:
            return self.name if self.name is not None else self.file.name
        return ''

    def __str__(self):
        return self.get_name()


class DeleteAttachmentTicketUpdate(TicketUpdate):
    """
    TicketUpdate to track the deletion of a previosuly uploaded attachment

    Automatically created by the default UI, if attachment management is enabled

    You should define a `delete_attachment` action to delete files, e.g.:

    ```python
    @action(
        verbose_name='Elimina file',
        ticket_update_model=TicketUpdate,
        priority=0,
    )
    @transition('state', 'new', 'new', permission=can_delete_attachment)
    @transition('state', 'rejected', 'rejected', permission=can_delete_attachment)
    def delete_attachment(self, ticket_update=None):
        return ticket_update
    ```
    """
    template_fields = 'django_fsm_ticket/partials/delete_attachment_tu_fields.html'

    attachment = models.ForeignKey(AttachmentTicketUpdate, on_delete=models.CASCADE)

    def __str__(self):
        return self.attachment.get_name()


class TicketEngine:
    def __init__(
        self,
        TicketClass,
        threshold_map=None,
        template_list='django_fsm_ticket/ticketlist.html',
        create_view=None,
        ticket_list_view=None,
        create_permission=None,
        base_template='base.html',
    ):
        """
        Ticket engine based on django_fsm

        @param TicketClass: subclass of Ticket
        @param threshold_map: a threshold map to categorize tickets based on priority
        @param template_list: template to show list of tickets
        @param create_view: DEPRECATED, user `create_view` on the ticket class instead
        @param ticket_list_view: DEPRECATED, use `list_view` on the ticket class instead
        @param create_permission: DEPRECATED, use `can_create` class function on the ticket class instead
        @param base_template: name of base template used in templates
        """
        self.TicketClass = TicketClass
        self.closed_states = TicketClass.closed_states
        if threshold_map is None:
            threshold_map = ThresholdMap((
                _('Closed'),
                0, _('Open'),
                0.5, _('Action required'),
            ))
        self.threshold_map = threshold_map
        self.template_list = template_list
        if create_view is not None:
            self.create_view = create_view
        elif TicketClass.create_view is not None:
            self.create_view = TicketClass.create_view
        else:
            self.create_view = ticket_create_view_factory(TicketClass).as_view()
        if ticket_list_view:
            self.ticket_list_view = ticket_list_view
        else:
            self.ticket_list_view = TicketClass.list_view
        if create_permission is not None:
            self.create_permission = create_permission
        else:
            self.create_permission = TicketClass.can_create
        self.base_template = base_template

        engine_registry[TicketClass.root_name] = self
        self.actions = {}
        for name in dir(TicketClass):
            method = getattr(TicketClass, name)
            if hasattr(method, "action"):
                self.actions[method.action.name] = method.action

    def filter_out_closed(self, ticket_qs):
        """
        Filter out closed tickets from a queryset of tickets
        """
        return ticket_qs.exclude(state__in=self.closed_states)

    def get_ticket_actions(self, t, user):
        """
        Return list of actions for ticket t, sorted by priority (from highest)
        """
        actions = set()
        for trans in t.get_available_user_state_transitions(user):
            actions.add(self.actions[trans.name])
        return list(sorted(actions, key=lambda a: (-a.priority, a.name)))

    def get_actions_and_priority(self, tickets, user, actions_for_closed_tickets=False):
        """
        Enhance ticket list with available actions and priorities

        Return a list of dictionaries with keys (ticket, actions, priority, category) sorted by priority
        """
        out = []
        for t in tickets:
            if t.state in self.closed_states:
                p = -1
                if actions_for_closed_tickets:
                    actions = self.get_ticket_actions(t, user)
                else:
                    actions = []
            else:
                actions = self.get_ticket_actions(t, user)
                if len(actions) > 0:
                    p = actions[0].priority
                else:
                    p = 0
            out.append({
                'ticket': t,
                'actions': actions,
                'priority': p,
                'category': self.threshold_map[p],
            })
            out.sort(key=lambda t: (t['priority'], t['ticket'].ts), reverse=True)

        return out

    def categorize_by_priority(self, tickets, user, actions_for_closed_tickets=False):
        """
        Divide tickets in groups by priority

        Return a list of dictionaries with keys: (category, ticket_dicts)
        ticket_dicts is a list of dictionaries with keys: (ticket, actions, priority, category)
        """
        categories = {}
        out = []
        for i, c in enumerate(self.threshold_map.get_values()):
            out.append({'category': c, 'ticket_dicts': []})
            categories[c] = i
        all_a_p = self.get_actions_and_priority(tickets, user, actions_for_closed_tickets)
        for d in all_a_p:
            out[categories[d['category']]]['ticket_dicts'].append(d)
        return out

    def set_view(self, action_name, view):
        """
        Set the view for serving an action
        """
        self.actions[action_name].view = view

    def create_ticket_view(self, request, root_name, *args, **kwargs):
        if self.create_permission(request.user):
            return self.create_view(request, root_name, *args, **kwargs)
        return redirect_to_login(request.path)

    def get_base_context(self):
        return {
            'base_template': self.base_template,
            'root_name': self.TicketClass.root_name,
        }


