from unittest.mock import patch

from django.contrib.auth.models import User
from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from django_fsm_ticket.models import AttachmentTicketUpdate, DeleteAttachmentTicketUpdate
from tests.models import PurchaseTicket


class DeleteAttachmentTest(TestCase):
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
            user=self.user, amount=1000, title="Test title", state="ordered"
        )
        self.attachment = AttachmentTicketUpdate(user=self.user)
        self.ticket.purchased(self.attachment)
        self.ticket.save()

    def delete(self, user, enforce_csrf_checks=False):
        client = Client(enforce_csrf_checks=enforce_csrf_checks)
        client.force_login(user=user)
        url = reverse("ticket_delete_attachment", args=(self.attachment.id, self.attachment.uuid))
        return client.post(url)

    def assertNotDeleted(self):
        self.attachment.refresh_from_db()
        self.assertFalse(self.attachment.deleted)
        self.assertFalse(DeleteAttachmentTicketUpdate.objects.exists())

    def test_delete_attachment(self):
        response = self.delete(self.admin)
        self.assertEqual(302, response.status_code)
        self.attachment.refresh_from_db()
        self.assertTrue(self.attachment.deleted)
        d = DeleteAttachmentTicketUpdate.objects.get()
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket, d.ticket)
        self.assertEqual(self.attachment.seq + 1, d.seq)
        self.assertEqual(d.pk, self.ticket.last_update.pk)

    def test_delete_attachment_state_not_allowed(self):
        self.ticket.state = "approved"
        self.ticket.save()
        response = self.delete(self.admin)
        self.assertEqual(404, response.status_code)
        self.assertNotDeleted()

    def test_delete_attachment_transition_permission(self):
        """
        can_delete_attachment is not enough: transition permission is required too
        """
        response = self.delete(self.user)
        self.assertEqual(403, response.status_code)
        self.assertNotDeleted()

    def test_delete_attachment_twice(self):
        self.assertEqual(302, self.delete(self.admin).status_code)
        self.assertEqual(404, self.delete(self.admin).status_code)
        self.assertEqual(1, DeleteAttachmentTicketUpdate.objects.count())

    def test_delete_attachment_after_concurrent_action(self):
        """
        State changed by a concurrent action after the initial checks: nothing is deleted
        """
        self.ticket.state = "approved"
        self.ticket.save()
        with patch("django_fsm_ticket.views.can_proceed", return_value=True), \
                patch("django_fsm_ticket.views.has_transition_perm", return_value=True):
            response = self.delete(self.admin)
        self.assertEqual(404, response.status_code)
        self.assertNotDeleted()

    def test_delete_attachment_csrf(self):
        """
        Without a CSRF token, nothing is deleted, even without CsrfViewMiddleware
        """
        middleware = [
            m for m in settings.MIDDLEWARE if m != "django.middleware.csrf.CsrfViewMiddleware"
        ]
        with override_settings(MIDDLEWARE=middleware):
            response = self.delete(self.admin, enforce_csrf_checks=True)
        self.assertEqual(403, response.status_code)
        self.assertNotDeleted()
