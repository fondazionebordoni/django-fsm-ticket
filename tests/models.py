from django_fsm_ticket.models import (
    AttachmentTicketUpdate,
    Ticket,
    TicketEngine,
    TicketUpdate,
    action,
    transition,
)
from django.db import models


def is_superuser(user):
    return user.is_superuser


class OrderTicketUpdate(TicketUpdate):
    template_fields = "purchase/partials/orderticketupdate_fields.html"
    form = "tests.forms.OrderTicketUpdateForm"
    price = models.FloatField()
    seller = models.CharField(max_length=1000)


class PurchaseTicket(Ticket):
    root_name = "purchase"
    amount = models.FloatField()

    verbose_states = {
        "new": "New",
        "rejected": "Rejected",
        "approved": "Approved",
        "ordered": "Ordered",
        "purchased": "Purchased",
        "canceled": "Canceled",
    }

    closed_states = {"rejected", "canceled", "purchased"}

    def is_visible(self, request):
        if request.user.is_anonymous:
            return False
        if request.user.is_superuser:
            return True
        return self.user == request.user

    def on_ticket_created(self):
        super().on_ticket_created()
        if self.amount < 10:
            self.approve()
        elif self.amount > 1000000:
            self.reject()

    def can_modify(self, user):
        return self.only_admin(user)

    @classmethod
    def prefilter_visible(cls, request):
        if request.user.is_anonymous:
            return cls.objects.none()
        if request.user.is_superuser:
            return cls.objects.all()
        return cls.objects.filter(user=request.user)

    def only_admin(self, user):
        return is_superuser(user)

    @action(priority=0.1, verbose_name="Comment")
    @transition("state", "new", "new")
    @transition("state", "rejected", "rejected")
    @transition("state", "approved", "approved")
    @transition("state", "ordered", "ordered")
    @transition("state", "purchased", "purchased")
    def comment(self, ticket_update=None):
        """
        Add a comment without changing ticket state
        """
        return ticket_update

    @action(priority=1, verbose_name="Approve")
    @transition("state", "new", "approved", permission=only_admin)
    @transition("state", "rejected", "approved", permission=only_admin)
    def approve(self, ticket_update=None):
        self.notify(ticket_update)
        return ticket_update

    @action(
        ticket_update_model=OrderTicketUpdate,
        verbose_name="Order",
    )
    @transition("state", "approved", "ordered")
    def order(self, ticket_update=None):
        return ticket_update

    @action(
        ticket_update_model=AttachmentTicketUpdate,
        verbose_name="Purchased",
    )
    @transition("state", "ordered", "purchased")
    def purchased(self, ticket_update=None):
        if ticket_update.file:
            ticket_update.name = ticket_update.file.name
        return ticket_update


purchase_ticket_engine = TicketEngine(
    TicketClass=PurchaseTicket,
)
