from unittest.mock import patch
from django_fsm import TransitionNotAllowed
from rest_framework import generics
from rest_framework.test import APIClient

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from django_fsm_ticket.models import TicketUpdate
from tests.models import PurchaseTicket, purchase_ticket_engine


class StaleTicketTest(TestCase):
    """
    Actions shall be checked and executed against the current state of the ticket,
    not against an instance loaded before a concurrent action was committed
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin",
            email="testadmin@fub.it",
            password="top_secret",
            is_superuser=True,
        )
        self.ticket = PurchaseTicket.objects.create(user=self.admin, amount=1000, title="Test title")
        # Loaded before a concurrent action is committed
        self.stale = PurchaseTicket.objects.get(pk=self.ticket.pk)

    def html_action(self, action_name):
        action = purchase_ticket_engine.actions[action_name]
        request = RequestFactory().post("/", {"notes": "Concurrent"})
        request.user = self.admin
        return action.view(request, purchase_ticket_engine.get_base_context(), self.stale, action)

    def api_action(self, action_name):
        client = APIClient()
        client.force_login(user=self.admin)
        url = reverse(
            "ticket-detail-action",
            kwargs={"root_name": "purchase", "pk": self.ticket.pk, "action_name": action_name},
        )
        with patch.object(generics.GenericAPIView, "get_object", return_value=self.stale):
            return client.put(url, {"notes": "Concurrent"}, format="json")

    def assertChain(self):
        self.ticket.refresh_from_db()
        updates = list(TicketUpdate.objects.filter(ticket=self.ticket).order_by("seq"))
        self.assertEqual(list(range(1, len(updates) + 1)), [tu.seq for tu in updates])
        self.assertEqual([None] + updates[:-1], [tu.previous for tu in updates])
        self.assertEqual(updates[-1], self.ticket.last_update)

    def test_html_action_no_longer_allowed(self):
        self.ticket.approve(TicketUpdate(user=self.admin))
        response = self.html_action("approve")
        self.assertEqual(200, response.status_code)  # Form with error
        self.assertEqual(1, TicketUpdate.objects.count())
        self.assertChain()

    def test_html_action_after_concurrent_update(self):
        self.ticket.comment(TicketUpdate(user=self.admin))
        response = self.html_action("comment")
        self.assertEqual(302, response.status_code)
        self.assertEqual(2, TicketUpdate.objects.count())
        self.assertChain()

    def test_api_action_no_longer_allowed(self):
        self.ticket.approve(TicketUpdate(user=self.admin))
        response = self.api_action("approve")
        self.assertEqual(409, response.status_code)
        self.assertEqual(1, TicketUpdate.objects.count())
        self.assertChain()

    def test_api_action_after_concurrent_update(self):
        self.ticket.comment(TicketUpdate(user=self.admin))
        response = self.api_action("comment")
        self.assertEqual(201, response.status_code, response.data)
        self.assertEqual(2, TicketUpdate.objects.count())
        self.assertChain()

    def test_programmatic_action_no_longer_allowed(self):
        self.ticket.approve(TicketUpdate(user=self.admin))
        with self.assertRaises(TransitionNotAllowed):
            self.stale.approve(TicketUpdate(user=self.admin))
        self.assertEqual(1, TicketUpdate.objects.count())
        self.assertChain()

    def test_programmatic_action_after_concurrent_update(self):
        self.ticket.comment(TicketUpdate(user=self.admin))
        self.stale.comment(TicketUpdate(user=self.admin))
        self.assertEqual(2, TicketUpdate.objects.count())
        self.assertChain()

    def test_programmatic_action_keeps_unsaved_changes(self):
        self.stale.amount = 5
        self.stale.comment(TicketUpdate(user=self.admin))
        self.ticket.refresh_from_db()
        self.assertEqual(5, self.ticket.amount)
