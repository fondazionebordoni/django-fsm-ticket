import tempfile
import uuid
from unittest.mock import patch
from rest_framework.test import APIClient

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from django_fsm_ticket.base_views import ActionError
from django_fsm_ticket.models import AttachmentTicketUpdate, TicketUpdate
from tests.models import PurchaseTicket


def prefilter_all(cls, request):
    return cls.objects.all()


def visible_to_superusers(self, request):
    return request.user.is_superuser


class InfoLeaksTest(TestCase):
    def setUp(self):
        media_root = tempfile.TemporaryDirectory()
        self.addCleanup(media_root.cleanup)
        media_settings = override_settings(MEDIA_ROOT=media_root.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        self.user = User.objects.create_user(
            username="testuser", email="test@fub.it", password="top_secret"
        )
        self.other_user = User.objects.create_user(
            username="otheruser", email="other@fub.it", password="top_secret"
        )
        self.admin = User.objects.create_user(
            username="admin",
            email="testadmin@fub.it",
            password="top_secret",
            is_superuser=True,
        )
        self.ticket = PurchaseTicket.objects.create(
            user=self.user, amount=2, title="Test title", state="ordered"
        )

    def api_get(self, user, url, params=None):
        client = APIClient()
        client.force_login(user=user)
        return client.get(url, params)

    def html_client(self, user):
        client = Client()
        client.force_login(user=user)
        return client

    def test_no_ordering_by_excluded_fields(self):
        cheaper = PurchaseTicket.objects.create(user=self.user, amount=1, title="Cheaper")
        url = reverse("ticket-list", kwargs={"root_name": "purchase"})
        with patch.object(PurchaseTicket, "exclude_fields", ["amount"]):
            response = self.api_get(self.user, url, {"ordering": "-amount"})
            # Default ordering (-id) instead of -amount
            self.assertEqual([cheaper.pk, self.ticket.pk], [t["id"] for t in response.data])
            response = self.api_get(self.user, url, {"ordering": "title"})
            self.assertEqual([cheaper.pk, self.ticket.pk], [t["id"] for t in response.data])
            response = self.api_get(self.user, url, {"ordering": "-title"})
            self.assertEqual([self.ticket.pk, cheaper.pk], [t["id"] for t in response.data])

    def test_deleted_attachment_file_hidden(self):
        self.ticket.purchased(AttachmentTicketUpdate(user=self.user, file=SimpleUploadedFile("a.pdf", b"x")))
        AttachmentTicketUpdate.objects.update(deleted=True)
        list_url = reverse("ticket-update-list", kwargs={"root_name": "purchase"})
        detail_url = reverse("ticket-detail", kwargs={"root_name": "purchase", "pk": self.ticket.pk})
        for user, hidden in ((self.user, True), (self.admin, False)):
            files = [tu["file"] for tu in self.api_get(user, list_url).data if "file" in tu]
            files += [tu["file"] for tu in self.api_get(user, detail_url).data["updates"] if "file" in tu]
            self.assertEqual(2, len(files))
            for file in files:
                self.assertEqual(hidden, file is None)

    def test_not_visible_ticket_not_found(self):
        url = reverse("ticket-detail", kwargs={"root_name": "purchase", "pk": self.ticket.pk})
        with patch.object(PurchaseTicket, "prefilter_visible", classmethod(prefilter_all)):
            self.assertEqual(404, self.api_get(self.other_user, url).status_code)

    def test_not_visible_ticket_update_not_found(self):
        tu = TicketUpdate(user=self.admin)
        self.ticket.comment(tu)
        url = reverse("ticket-update-detail", kwargs={"root_name": "purchase", "pk": tu.pk})
        with patch.object(TicketUpdate, "is_visible", visible_to_superusers):
            self.assertEqual(404, self.api_get(self.user, url).status_code)

    def test_missing_and_not_visible_attachments_look_the_same(self):
        self.ticket.purchased(AttachmentTicketUpdate(user=self.user, file=SimpleUploadedFile("a.pdf", b"x")))
        atu = AttachmentTicketUpdate.objects.get()
        not_visible = self.html_client(self.other_user).get(reverse("ticket_download_attachment", args=(atu.pk, atu.uuid)))
        missing = self.html_client(self.other_user).get(reverse("ticket_download_attachment", args=(atu.pk + 1, atu.uuid)))
        self.assertEqual(302, not_visible.status_code)
        self.assertEqual(not_visible.status_code, missing.status_code)

    def test_invalid_uuid_not_found(self):
        self.ticket.purchased(AttachmentTicketUpdate(user=self.user, file=SimpleUploadedFile("a.pdf", b"x")))
        atu = AttachmentTicketUpdate.objects.get()
        client = self.html_client(self.user)
        self.assertEqual(404, client.get(f"/download_attachment/{atu.pk}/not-a-uuid").status_code)
        url = reverse("ticket_download_attachment", args=(atu.pk, uuid.uuid4()))
        self.assertEqual(404, client.get(url).status_code)

    def test_api_action_error(self):
        url = reverse(
            "ticket-detail-action",
            kwargs={"root_name": "purchase", "pk": self.ticket.pk, "action_name": "comment"},
        )
        client = APIClient()
        client.force_login(user=self.user)
        with patch.object(PurchaseTicket, "_add_update", side_effect=ActionError("Not now")):
            response = client.put(url, {"notes": "Hello"}, format="json")
        self.assertEqual(400, response.status_code)
        self.assertEqual(["Not now"], response.data["non_field_errors"])
        self.assertFalse(TicketUpdate.objects.exists())
