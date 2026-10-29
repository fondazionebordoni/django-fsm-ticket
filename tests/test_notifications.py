from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase

from django_fsm_ticket.models import TicketUpdate
from tests.models import PurchaseTicket


def visible_to_superusers(self, request):
    return request.user.is_superuser


class NotificationVisibilityTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@fub.it", password="top_secret"
        )
        self.admin = User.objects.create_user(
            username="admin",
            email="testadmin@fub.it",
            password="top_secret",
            is_superuser=True,
        )
        self.ticket = PurchaseTicket.objects.create(
            user=self.user,
            amount=1000,
            title="Test title",
        )

    def test_visible_update_is_notified(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.ticket.approve(TicketUpdate(notes="Approved", user=self.admin))
        self.assertEqual(1, len(mail.outbox))
        self.assertIn("test@fub.it", mail.outbox[0].to[0])
        self.assertIn("Approved", mail.outbox[0].body)

    def test_hidden_update_is_not_notified(self):
        with patch.object(TicketUpdate, "is_visible", visible_to_superusers), \
                self.captureOnCommitCallbacks(execute=True):
            self.ticket.approve(TicketUpdate(notes="Internal note", user=self.admin))
        self.assertEqual([], mail.outbox)

    def test_hidden_update_is_not_notified_to_additional_users(self):
        with patch.object(TicketUpdate, "is_visible", visible_to_superusers), \
                self.captureOnCommitCallbacks(execute=True):
            tu = TicketUpdate(notes="Internal note", user=self.admin)
            self.ticket.comment(tu)
            self.ticket.notify(tu, users=[], additional_users=[self.user])
        self.assertEqual([], mail.outbox)

    def test_hidden_update_is_not_sent_as_reminder(self):
        with patch.object(TicketUpdate, "is_visible", visible_to_superusers):
            with self.captureOnCommitCallbacks(execute=True):
                self.ticket.state = "approved"
                self.ticket.save()
                self.ticket.comment(TicketUpdate(notes="Internal note", user=self.admin))
            mail.outbox.clear()
            with self.captureOnCommitCallbacks(execute=True):
                PurchaseTicket.send_reminders()
        self.assertEqual([], mail.outbox)
