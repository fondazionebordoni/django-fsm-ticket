import base64
import json
import tempfile
from unittest.mock import patch

from rest_framework.test import APIClient

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from django_fsm_ticket.models import AttachmentTicketUpdate, Ticket, TicketUpdate

from tests.models import PurchaseTicket


def prefilter_public_for_anonymous(cls, request):
    """
    Anonymous users may see all tickets (e.g. public ones), other users only their own
    """
    if request.user.is_anonymous:
        return cls.objects.all()
    return cls.objects.filter(user=request.user)


class NonSessionAuthenticationTest(TestCase):
    """
    API views compute visibility for the user authenticated by DRF, not for the session user

    With authentication methods other than the session (Basic, Token, JWT...), the user
    of the Django request is anonymous: prefilter_visible() must not receive it.
    """

    password = "top_secret"

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@fub.it", password=self.password
        )
        self.other_user = User.objects.create_user(
            username="otheruser", email="other@fub.it", password=self.password
        )
        self.ticket = PurchaseTicket.objects.create(
            user=self.user, amount=1000, title="Own ticket"
        )
        self.other_ticket = PurchaseTicket.objects.create(
            user=self.other_user, amount=1000, title="Other ticket"
        )
        self.list_url = reverse("ticket-list", kwargs={"root_name": "purchase"})

    def detail_url(self, ticket):
        return reverse("ticket-detail", kwargs={"root_name": "purchase", "pk": ticket.pk})

    def action_url(self, ticket, action_name="comment"):
        return reverse(
            "ticket-detail-action",
            kwargs={"root_name": "purchase", "pk": ticket.pk, "action_name": action_name},
        )

    def basic_client(self, user):
        client = APIClient()
        credentials = base64.b64encode(f"{user.username}:{self.password}".encode()).decode()
        client.credentials(HTTP_AUTHORIZATION=f"Basic {credentials}")
        return client

    def comment(self, client, ticket):
        return client.patch(
            self.action_url(ticket),
            data=json.dumps({"notes": "A note"}),
            content_type="application/json",
        )

    def test_list_basic_auth(self):
        response = self.basic_client(self.user).get(self.list_url)
        self.assertEqual(200, response.status_code)
        self.assertEqual([self.ticket.pk], [t["id"] for t in response.data])

    def test_detail_basic_auth(self):
        client = self.basic_client(self.user)
        self.assertEqual(200, client.get(self.detail_url(self.ticket)).status_code)
        self.assertEqual(404, client.get(self.detail_url(self.other_ticket)).status_code)

    def test_action_basic_auth(self):
        client = self.basic_client(self.user)
        self.assertEqual(201, self.comment(client, self.ticket).status_code)
        self.assertEqual(404, self.comment(client, self.other_ticket).status_code)
        self.assertEqual(1, TicketUpdate.objects.count())

    def test_prefilter_wider_for_anonymous(self):
        """
        Authenticated users do not get what prefilter_visible() grants to anonymous users

        is_visible() is not overridden, so prefilter_visible() alone decides visibility.
        """
        client = self.basic_client(self.user)
        with patch.object(
            PurchaseTicket, "prefilter_visible", classmethod(prefilter_public_for_anonymous)
        ), patch.object(PurchaseTicket, "is_visible", Ticket.is_visible):
            response = client.get(self.list_url)
            self.assertEqual([self.ticket.pk], [t["id"] for t in response.data])
            self.assertEqual(404, client.get(self.detail_url(self.other_ticket)).status_code)
            self.assertEqual(404, self.comment(client, self.other_ticket).status_code)
        self.assertEqual(0, TicketUpdate.objects.count())

    def test_session_and_basic_auth(self):
        """
        Session and Basic credentials of different users are not combined

        DRF tries session authentication first, so the session user is the authenticated one.
        """
        client = self.basic_client(self.other_user)
        client.force_login(self.user)
        response = client.get(self.list_url)
        self.assertEqual([self.ticket.pk], [t["id"] for t in response.data])
        self.assertEqual(404, client.get(self.detail_url(self.other_ticket)).status_code)

    def test_attachment_download_basic_auth(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            PurchaseTicket.objects.filter(pk=self.ticket.pk).update(state="ordered")
            self.ticket.refresh_from_db()
            self.ticket.purchased(
                AttachmentTicketUpdate(user=self.user, file=SimpleUploadedFile("a.pdf", b"content"))
            )
            attachment = AttachmentTicketUpdate.objects.get()
            url = reverse("ticket-update-download", kwargs={"root_name": "purchase", "pk": attachment.pk})
            response = self.basic_client(self.user).get(url)
            self.assertEqual(200, response.status_code)
            self.assertEqual(b"content", b"".join(response.streaming_content))
            self.assertEqual(404, self.basic_client(self.other_user).get(url).status_code)
