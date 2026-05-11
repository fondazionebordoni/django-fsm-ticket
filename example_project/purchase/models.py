from django.db import models
from django_fsm_ticket.models import Ticket, TicketEngine, TicketUpdate, AttachmentTicketUpdate
from django_fsm_ticket.models import action, transition


class OrderTicketUpdate(TicketUpdate):
    template_fields = 'purchase/partials/orderticketupdate_fields.html'

    price = models.FloatField()
    seller = models.CharField(max_length=1000)


class PurchaseTicket(Ticket):
    root_name = 'purchase'
    template_fields = 'purchase/partials/ticket_content_fields.html'

    verbose_states = {
        'new': "New",
        'rejected': "Rejected",
        'approved': "Approved",
        'ordered': "Ordered",
        'purchased': "Purchased",
        'canceled': "Canceled",
    }

    closed_states = {'rejected', 'canceled', 'purchased'}

    amount = models.FloatField()

    # Ticket visibility

    def is_visible(self, request):
        if request.user.is_anonymous:
            return False
        if request.user.is_superuser:
            return True
        return self.user == request.user

    @classmethod
    def prefilter_visible(cls, request):
        if request.user.is_anonymous:
            return cls.objects.none()
        if request.user.is_superuser:
            return cls.objects.all()
        return cls.objects.filter(user=request.user)

    # Conditions for permissions

    def is_superuser(self, user):
        return user.is_superuser

    # Ticket actions

    @action(priority=1, verbose_name="Approve")
    @transition('state', 'new', 'approved', permission=is_superuser)
    @transition('state', 'rejected', 'approved', permission=is_superuser)
    def approve(self, ticket_update=None):
        self.notify(ticket_update, additional_users=[self.user])
        return ticket_update

    @action(verbose_name="Reject")
    @transition('state', 'new', 'rejected', permission=is_superuser)
    def reject(self, ticket_update=None):
        return ticket_update

    @action(priority=0.1, verbose_name="Comment")
    @transition('state', 'new', 'new')
    @transition('state', 'rejected', 'rejected')
    @transition('state', 'approved', 'approved')
    @transition('state', 'ordered', 'ordered')
    @transition('state', 'purchased', 'purchased')
    def comment(self, ticket_update=None):
        """
        Add a comment without changing ticket state
        """
        return ticket_update

    @action(
        ticket_update_model=OrderTicketUpdate,
        verbose_name='Order',
    )
    @transition('state', 'approved', 'ordered', permission=is_superuser)
    def order(self, ticket_update=None):
        return ticket_update

    @action(
        ticket_update_model=AttachmentTicketUpdate,
        verbose_name='Confirm purchased',
    )
    @transition('state', 'ordered', 'purchased', permission=is_superuser)
    def purchased(self, ticket_update=None):
        if ticket_update.file:
            ticket_update.name = ticket_update.file.name
        return ticket_update

    @action(
        verbose_name='Delete file',
        ticket_update_model=TicketUpdate,
        priority=0,
    )
    @transition('state', 'purchased', 'purchased', permission=is_superuser)
    def delete_attachment(self, ticket_update=None):
        return ticket_update

    def on_ticket_created(self):
        super().on_ticket_created()
        if self.amount < 10:
            self.approve()
        elif self.amount > 1000000:
            self.reject()


purchase_ticket_engine = TicketEngine(
    TicketClass=PurchaseTicket,
)