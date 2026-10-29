from rest_framework.test import APIClient

from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.urls import reverse

from django_fsm_ticket.models import AttachmentTicketUpdate, DeleteAttachmentTicketUpdate
from django_fsm_ticket.serializers import ticket_update_create_serializer_factory
from tests.models import PurchaseTicket


class RelatedFieldsLimitTest(TestCase):
    """
    The action API shall apply limit_view_querysets to related fields
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@fub.it", password="top_secret"
        )
        self.ticket = self.purchased_ticket()
        self.attachment = self.ticket.last_update
        self.other_attachment = self.purchased_ticket().last_update

    def purchased_ticket(self):
        ticket = PurchaseTicket.objects.create(
            user=self.user, amount=1000, title="Test title", state="ordered"
        )
        ticket.purchased(AttachmentTicketUpdate(user=self.user))
        ticket.save()
        return ticket

    def report(self, attachment):
        client = APIClient()
        client.force_login(user=self.user)
        url = reverse(
            "ticket-detail-action",
            kwargs={"root_name": "purchase", "pk": self.ticket.pk, "action_name": "report_attachment"},
        )
        return client.put(url, {"attachment": attachment.pk}, format="json")

    def test_related_field_of_same_ticket(self):
        response = self.report(self.attachment)
        self.assertEqual(201, response.status_code, response.data)
        d = DeleteAttachmentTicketUpdate.objects.get()
        self.assertEqual(self.attachment.pk, d.attachment_id)

    def test_related_field_of_other_ticket(self):
        response = self.report(self.other_attachment)
        self.assertEqual(400, response.status_code)
        self.assertIn("attachment", response.data)
        self.assertFalse(DeleteAttachmentTicketUpdate.objects.exists())

    def test_serializer_requires_ticket(self):
        serializer_class = ticket_update_create_serializer_factory(DeleteAttachmentTicketUpdate)
        serializer = serializer_class(data={"attachment": self.attachment.pk})
        with self.assertRaises(ImproperlyConfigured):
            serializer.is_valid()
