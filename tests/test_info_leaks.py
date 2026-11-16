import tempfile
import uuid
from unittest.mock import patch
from rest_framework.test import APIClient

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from django_fsm_ticket import api
from django_fsm_ticket.base_views import ActionError
from django_fsm_ticket.models import AttachmentTicketUpdate, DeleteAttachmentTicketUpdate, TicketUpdate
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

    def attachment(self, name="a.pdf", content=b"content"):
        self.ticket.purchased(AttachmentTicketUpdate(user=self.user, file=SimpleUploadedFile(name, content)))
        return AttachmentTicketUpdate.objects.get()

    def download_url(self, ticket_update):
        return reverse("ticket-update-download", kwargs={"root_name": "purchase", "pk": ticket_update.pk})

    def test_attachment_file_is_download_url(self):
        """The API provides the URL of its download endpoint, not the URL of the file in the storage"""
        attachment = self.attachment()
        download_url = "http://testserver" + self.download_url(attachment)
        urls = [
            reverse("ticket-update-list", kwargs={"root_name": "purchase"}),
            reverse("ticket-detail", kwargs={"root_name": "purchase", "pk": self.ticket.pk}),
            reverse("ticket-update-detail", kwargs={"root_name": "purchase", "pk": attachment.pk}),
        ]
        for url in urls:
            with self.subTest(url=url):
                data = self.api_get(self.user, url).data
                if isinstance(data, dict):
                    data = data.get("updates", [data])
                self.assertEqual([download_url], [tu["file"] for tu in data if "file" in tu])

    def test_attachment_download(self):
        attachment = self.attachment()
        # Session authentication: the URL can also be used as a link in HTML pages
        response = self.html_client(self.user).get(self.download_url(attachment))
        self.assertEqual(200, response.status_code)
        self.assertEqual(b"content", b"".join(response.streaming_content))
        self.assertEqual('attachment; filename="a.pdf"', response["Content-Disposition"])
        self.assertIn("private", response["Cache-Control"])

    def test_attachment_download_visibility(self):
        attachment = self.attachment()
        url = self.download_url(attachment)
        self.assertIn(APIClient().get(url).status_code, (401, 403))
        self.assertEqual(404, self.api_get(self.other_user, url).status_code)
        missing = reverse("ticket-update-download", kwargs={"root_name": "purchase", "pk": attachment.pk + 100})
        self.assertEqual(404, self.api_get(self.user, missing).status_code)
        with patch.object(AttachmentTicketUpdate, "is_visible", visible_to_superusers):
            self.assertEqual(404, self.api_get(self.user, url).status_code)
            self.assertEqual(200, self.api_get(self.admin, url).status_code)

    def test_attachment_download_not_an_attachment(self):
        self.ticket.comment(TicketUpdate(user=self.user, notes="No attachment"))
        url = self.download_url(TicketUpdate.objects.get())
        self.assertEqual(404, self.api_get(self.user, url).status_code)

    def test_attachment_download_without_file(self):
        self.ticket.purchased(AttachmentTicketUpdate(user=self.user))
        url = self.download_url(AttachmentTicketUpdate.objects.get())
        self.assertEqual(404, self.api_get(self.user, url).status_code)

    def test_deleted_attachment_download(self):
        attachment = self.attachment()
        AttachmentTicketUpdate.objects.update(deleted=True)
        url = self.download_url(attachment)
        self.assertEqual(404, self.api_get(self.user, url).status_code)
        self.assertEqual(200, self.api_get(self.admin, url).status_code)

    def test_attachment_without_file(self):
        self.ticket.purchased(AttachmentTicketUpdate(user=self.user))
        url = reverse("ticket-update-list", kwargs={"root_name": "purchase"})
        self.assertEqual([None], [tu["file"] for tu in self.api_get(self.user, url).data if "file" in tu])

    def hidden_attachment(self):
        self.ticket.purchased(
            AttachmentTicketUpdate(user=self.user, file=SimpleUploadedFile("internal-report.pdf", b"x"))
        )
        self.ticket.save()
        return AttachmentTicketUpdate.objects.get()

    def test_deletion_of_hidden_attachment_is_hidden(self):
        """The update that tracks a deletion shows the name of the attachment: same visibility"""
        attachment = self.hidden_attachment()
        delete_url = reverse("ticket_delete_attachment", args=[attachment.pk, attachment.uuid])
        ticket_url = reverse("single_ticket", args=["purchase", self.ticket.pk])
        api_url = reverse("ticket-detail", kwargs={"root_name": "purchase", "pk": self.ticket.pk})
        list_url = reverse("ticket-update-list", kwargs={"root_name": "purchase"})
        with patch.object(AttachmentTicketUpdate, "is_visible", visible_to_superusers):
            self.assertEqual(302, self.html_client(self.admin).post(delete_url).status_code)
            deletion = DeleteAttachmentTicketUpdate.objects.get()
            detail_url = reverse("ticket-update-detail", kwargs={"root_name": "purchase", "pk": deletion.pk})
            self.assertNotIn("internal-report", self.html_client(self.user).get(ticket_url).content.decode())
            self.assertIn("internal-report", self.html_client(self.admin).get(ticket_url).content.decode())
            for user, visible in ((self.user, False), (self.admin, True)):
                updates = self.api_get(user, api_url).data["updates"]
                self.assertEqual(visible, deletion.pk in [tu["id"] for tu in updates])
                updates = self.api_get(user, list_url).data
                self.assertEqual(visible, deletion.pk in [tu["id"] for tu in updates])
                self.assertEqual(200 if visible else 404, self.api_get(user, detail_url).status_code)
        # Without visibility rules on attachments, the deletion is visible to everyone who sees the ticket
        self.assertIn("internal-report", self.html_client(self.user).get(ticket_url).content.decode())

    def test_delete_attachment_is_visible_is_default(self):
        """
        Without visibility rules on attachments, lists of ticket updates are paginated in the database
        """
        self.assertFalse(api.overrides_is_visible(TicketUpdate))
        with patch.object(AttachmentTicketUpdate, "is_visible", visible_to_superusers):
            self.assertTrue(api.overrides_is_visible(TicketUpdate))

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
