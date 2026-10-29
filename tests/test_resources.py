import tempfile
import warnings
from unittest.mock import patch
from rest_framework.test import APIClient

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from django_fsm_ticket.models import AttachmentTicketUpdate, TicketUpdate
from tests.models import PurchaseTicket


class Config:
    API_MAX_LIMIT = 2


class ResourceLimitsTest(TestCase):
    def setUp(self):
        media_root = tempfile.TemporaryDirectory()
        self.addCleanup(media_root.cleanup)
        media_settings = override_settings(MEDIA_ROOT=media_root.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
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

    def api_client(self):
        client = APIClient()
        client.force_login(user=self.user)
        return client

    # API page size

    def test_api_page_size_not_limited_by_default(self):
        for i in range(2):
            PurchaseTicket.objects.create(user=self.user, amount=1000, title=f"Other {i}")
        response = self.api_client().get(reverse("ticket-list", kwargs={"root_name": "purchase"}))
        self.assertEqual(3, len(response.data))

    def test_api_page_size_limited_by_config(self):
        for i in range(2):
            PurchaseTicket.objects.create(user=self.user, amount=1000, title=f"Other {i}")
        url = reverse("ticket-list", kwargs={"root_name": "purchase"})
        with patch("django_fsm_ticket.api.fsm_tickets_config", Config):
            for params in ({}, {"limit": 100}):
                data = self.api_client().get(url, params).data
                self.assertEqual(3, data["count"])
                self.assertEqual(2, len(data["results"]))
                self.assertIsNotNone(data["next"])

    def test_api_max_limit_unset_warns(self):
        url = reverse("ticket-list", kwargs={"root_name": "purchase"})
        with patch("django_fsm_ticket.api.fsm_tickets_config", object()), \
                self.assertWarnsRegex(FutureWarning, "API_MAX_LIMIT"):
            response = self.api_client().get(url)
        # Unbounded, not paginated, as before 2.0
        self.assertEqual(1, len(response.data))

    def test_api_max_limit_none_does_not_warn(self):
        url = reverse("ticket-list", kwargs={"root_name": "purchase"})

        class NoLimitConfig:
            API_MAX_LIMIT = None

        with patch("django_fsm_ticket.api.fsm_tickets_config", NoLimitConfig), \
                warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            response = self.api_client().get(url)
        self.assertEqual(1, len(response.data))

    # Attachments

    def purchased_html(self, content):
        client = Client()
        client.force_login(user=self.user)
        url = reverse(
            "ticket_action",
            kwargs={"root_name": "purchase", "pk": self.ticket.pk, "action_name": "purchased"},
        )
        return client.post(url, {"file": SimpleUploadedFile("invoice.pdf", content)})

    def purchased_api(self, content):
        url = reverse(
            "ticket-detail-action",
            kwargs={"root_name": "purchase", "pk": self.ticket.pk, "action_name": "purchased"},
        )
        return self.api_client().put(url, {"file": SimpleUploadedFile("invoice.pdf", content)})

    def test_download_attachment(self):
        self.assertEqual(201, self.purchased_api(b"Invoice").status_code)
        atu = AttachmentTicketUpdate.objects.get()
        client = Client()
        client.force_login(user=self.user)
        with patch("django_fsm_ticket.utils.copy_django_modelfile_to_dir") as copy:
            response = client.get(reverse("ticket_download_attachment", args=(atu.id, atu.uuid)))
            self.assertEqual(200, response.status_code)
            self.assertEqual(b"Invoice", b"".join(response.streaming_content))
        copy.assert_not_called()
        self.assertIn('filename="invoice.pdf"', response["Content-Disposition"])

    def test_download_attachment_without_file(self):
        self.ticket.purchased(AttachmentTicketUpdate(user=self.user))
        atu = AttachmentTicketUpdate.objects.get()
        client = Client()
        client.force_login(user=self.user)
        response = client.get(reverse("ticket_download_attachment", args=(atu.id, atu.uuid)))
        self.assertEqual(404, response.status_code)

    def test_attachment_size_limit_html(self):
        with patch.object(AttachmentTicketUpdate, "max_file_size", 10):
            response = self.purchased_html(b"x" * 11)
            self.assertEqual(200, response.status_code)  # Form with error
            self.assertIn("file", response.context["form"].errors)
            self.assertFalse(AttachmentTicketUpdate.objects.exists())
            self.assertEqual(302, self.purchased_html(b"x" * 10).status_code)

    def test_attachment_size_limit_api(self):
        with patch.object(AttachmentTicketUpdate, "max_file_size", 10):
            response = self.purchased_api(b"x" * 11)
            self.assertEqual(400, response.status_code)
            self.assertIn("file", response.data)
            self.assertFalse(AttachmentTicketUpdate.objects.exists())
            self.assertEqual(201, self.purchased_api(b"x" * 10).status_code)

    # Active users

    def test_inactive_users_not_notified(self):
        inactive = User.objects.create_user(
            username="former", email="former@fub.it", password="top_secret",
            is_superuser=True, is_active=False,
        )
        self.assertNotIn(inactive, self.ticket.get_users_with_actions())
        tu = TicketUpdate(user=self.admin)
        self.ticket.comment(tu)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.ticket.notify(tu, users=[inactive], additional_users=[inactive, self.user])
        self.assertEqual(["test@fub.it"], [m.to[0].split("<")[-1].rstrip(">") for m in mail.outbox])
