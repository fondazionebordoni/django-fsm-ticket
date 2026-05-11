from django.contrib.auth.models import User
from django.http import Http404, HttpResponseForbidden
from django.test import Client, RequestFactory, TestCase, tag
from django.urls import reverse

from django_fsm_ticket.base_views import TicketListView
from django_fsm_ticket.views import ticket_action
from tests.models import PurchaseTicket


class TicketViewTest(TestCase):
    def setUp(self):
        # Every test needs access to the request factory.
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username="testuser", email="test@fub.it", password="top_secret"
        )

    def test_all_tickets_no_root(self):
        # Create an instance of a GET request.
        request = self.factory.get("/some_root_name")

        # Recall that middleware are not supported. You can simulate a
        # logged-in user by setting request.user manually.
        request.user = self.user

        # Or you can simulate an anonymous user by setting request.user to
        # an AnonymousUser instance.
        # request.user = AnonymousUser()

        # # Test my_view() as if it were deployed at /customer/details
        # response = my_view(request)
        # Use this syntax for class-based views.
        with self.assertRaises(Http404):
            TicketListView.as_view()(request, root_name="some_root_name")

    def test_all_tickets(self):
        request = self.factory.get("/purchase")
        request.user = self.user
        response = TicketListView.as_view()(request, root_name="purchase")
        self.assertEqual(response.status_code, 200)

    def test_all_tickets_get(self):
        """
        Do a real get
        """
        client = Client()
        client.force_login(user=self.user)
        PurchaseTicket.objects.create(user=self.user, amount=1000)
        response = client.get(
            reverse(
                "all_tickets",
                kwargs={
                    "root_name": "purchase",
                },
            ),
        )
        self.assertEqual(200, response.status_code)

    def test_context(self):
        request = RequestFactory().get("/")
        request.user = self.user
        view = TicketListView()
        view.setup(request, root_name="purchase")
        view.dispatch(request, root_name="purchase")
        context = view.get_context_data()
        self.assertEqual("purchase", context.get("root_name"))


@tag("actions")
class ActionsViewTest(TestCase):
    def setUp(self):
        # Every test needs access to the request factory.
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username="testuser", email="test@fub.it", password="top_secret"
        )
        self.admin = User.objects.create_user(
            username="admin",
            email="testadmin@fub.it",
            password="top_secret",
            is_superuser=True,
        )

    def test_ticket_action(self):
        request = self.factory.get("/some_root_name")
        with self.assertRaises(Http404):
            ticket_action(request, "some_root_name", 1, "approve")
        request = self.factory.get("/purchase")
        request.user = self.user
        ticket = PurchaseTicket.objects.create(user=self.user, amount=1000)
        res = ticket_action(request, "purchase", ticket.pk, "approve")
        self.assertTrue(isinstance(res, HttpResponseForbidden))

    def test_ticket_action_post(self):
        client = Client()
        client.force_login(user=self.user)
        ticket = PurchaseTicket.objects.create(user=self.user, amount=1000)
        response = client.post(
            reverse(
                "ticket_action",
                kwargs={
                    "root_name": "purchase",
                    "pk": str(ticket.pk),
                    "action_name": "approve",
                },
            ),
            data={},
        )
        self.assertEqual(403, response.status_code)
        client.force_login(user=self.admin)
        response = client.post(
            reverse(
                "ticket_action",
                kwargs={
                    "root_name": "purchase",
                    "pk": str(ticket.pk),
                    "action_name": "approve",
                },
            ),
            data={},
        )
        self.assertEqual(302, response.status_code)
        ticket.refresh_from_db()
        self.assertEqual("approved", ticket.state)
