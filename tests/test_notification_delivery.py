from email.utils import getaddresses
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.db import transaction
from django.test import TestCase

from django_fsm_ticket.models import TicketUpdate
from tests.models import PurchaseTicket


class NotificationDeliveryTest(TestCase):
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

    def approve(self):
        self.ticket.approve(TicketUpdate(notes="Approved", user=self.admin))

    def test_sent_after_commit(self):
        with self.captureOnCommitCallbacks() as callbacks:
            self.approve()
            self.assertEqual([], mail.outbox)
        self.assertEqual(1, len(callbacks))
        callbacks[0]()
        self.assertEqual(["test@fub.it"], [a for _, a in getaddresses(mail.outbox[0].to)])

    def test_not_sent_on_rollback(self):
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    self.approve()
                    raise RuntimeError("Something went wrong after the transition")
            except RuntimeError:
                pass
        self.assertEqual([], mail.outbox)

    def test_failure_does_not_affect_other_recipients(self):
        User.objects.create_user(
            username="admin2", email="testadmin2@fub.it", password="top_secret", is_superuser=True
        )
        original = PurchaseTicket.send_notification

        def send_notification(ticket, subject, message, recipient_user):
            if recipient_user == self.user:
                raise ConnectionRefusedError("SMTP server not available")
            original(ticket, subject, message, recipient_user)

        with patch.object(PurchaseTicket, "send_notification", send_notification), \
                self.assertLogs(level="ERROR"), \
                self.captureOnCommitCallbacks(execute=True):
            self.approve()
        self.assertEqual(["testadmin2@fub.it"], [a for m in mail.outbox for _, a in getaddresses(m.to)])
        self.ticket.refresh_from_db()
        self.assertEqual("approved", self.ticket.state)

    def test_recipient_name_is_quoted(self):
        self.user.username = "Doe, John"
        self.user.save()
        with self.captureOnCommitCallbacks(execute=True):
            self.approve()
        self.assertEqual([("Doe, John", "test@fub.it")], getaddresses(mail.outbox[0].to))

    def test_user_without_email_is_skipped(self):
        self.user.email = ""
        self.user.save()
        with self.assertLogs(level="WARNING"), \
                self.captureOnCommitCallbacks(execute=True):
            self.approve()
        self.assertEqual([], mail.outbox)
