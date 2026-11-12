from rest_framework.renderers import BrowsableAPIRenderer, JSONRenderer
from rest_framework.test import APIClient

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from django_fsm_ticket.api import api_renderers
from django_fsm_ticket.models import AttachmentTicketUpdate
from tests.models import PurchaseTicket


class BrowsableApiTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@fub.it", password="top_secret"
        )
        self.ticket = PurchaseTicket.objects.create(
            user=self.user, amount=1000, title="Test title", state="ordered"
        )
        self.ticket.purchased(AttachmentTicketUpdate(user=self.user, name="mine.pdf"))
        self.client = APIClient()
        self.client.force_login(user=self.user)
        self.list_url = reverse("ticket-list", kwargs={"root_name": "purchase"})

    def action_url(self, pk):
        return reverse(
            "ticket-detail-action",
            kwargs={"root_name": "purchase", "pk": pk, "action_name": "report_attachment"},
        )


class BrowsableApiDisabledTest(BrowsableApiTestCase):
    """
    Unless in DEBUG mode, the browsable API is not available
    """

    def test_renderers(self):
        self.assertNotIn(BrowsableAPIRenderer, api_renderers())
        self.assertIn(JSONRenderer, api_renderers())

    @override_settings(
        REST_FRAMEWORK={"DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.BrowsableAPIRenderer"]}
    )
    def test_json_if_no_renderer_left(self):
        self.assertEqual(api_renderers(), [JSONRenderer])

    @override_settings(
        REST_FRAMEWORK={
            "DEFAULT_RENDERER_CLASSES": [
                "rest_framework.renderers.AdminRenderer",
                "rest_framework.renderers.TemplateHTMLRenderer",
            ]
        }
    )
    def test_other_renderers_kept(self):
        self.assertEqual([r.__name__ for r in api_renderers()], ["TemplateHTMLRenderer"])

    def test_html_not_served(self):
        for url in [
            self.list_url,
            reverse("ticket-update-list", kwargs={"root_name": "purchase"}),
            reverse("ticket-detail", kwargs={"root_name": "purchase", "pk": self.ticket.pk}),
            reverse(
                "ticket-update-detail",
                kwargs={"root_name": "purchase", "pk": self.ticket.last_update.pk},
            ),
            self.action_url(self.ticket.pk),
        ]:
            with self.subTest(url=url):
                response = self.client.get(url, HTTP_ACCEPT="text/html")
                self.assertEqual(response.status_code, 406)
                response = self.client.get(url, {"format": "api"})
                self.assertEqual(response.status_code, 404)

    def test_json_served(self):
        response = self.client.get(self.list_url, HTTP_ACCEPT="text/html,*/*")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")




@override_settings(DEBUG=True)
class BrowsableApiDebugTest(TestCase):
    def test_renderers(self):
        self.assertIn(BrowsableAPIRenderer, api_renderers())
