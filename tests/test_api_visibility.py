from unittest.mock import patch
from rest_framework.test import APIClient

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from django_fsm_ticket import api
from django_fsm_ticket.models import Ticket, TicketUpdate

from tests.models import PurchaseTicket


def prefilter_all(cls, request):
    return cls.objects.all()


def visible_to_superusers(self, request):
    return request.user.is_superuser


class ListVisibilityTest(TestCase):
    """
    Counts and pages of list endpoints shall not depend on non-visible items
    """

    def setUp(self):
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
            user=self.user,
            amount=1000,
            title="Merger with ACME",
        )
        self.ticket_list_url = reverse("ticket-list", kwargs={"root_name": "purchase"})
        self.ticket_update_list_url = reverse(
            "ticket-update-list", kwargs={"root_name": "purchase"}
        )

    def get(self, user, url, params):
        client = APIClient()
        client.force_login(user=user)
        response = client.get(url, params)
        self.assertEqual(200, response.status_code, response.data)
        return response.data

    def test_ticket_list_count_excludes_not_visible(self):
        with patch.object(PurchaseTicket, "prefilter_visible", classmethod(prefilter_all)):
            data = self.get(self.other_user, self.ticket_list_url, {"search": "ACME", "limit": 1})
            self.assertEqual(0, data["count"])
            self.assertEqual([], data["results"])
            data = self.get(self.user, self.ticket_list_url, {"search": "ACME", "limit": 1})
            self.assertEqual(1, data["count"])
            self.assertEqual(self.ticket.pk, data["results"][0]["id"])

    def test_ticket_list_pages_only_contain_visible(self):
        own = [self.ticket]
        for i in range(2):
            PurchaseTicket.objects.create(user=self.other_user, amount=1000, title=f"Other {i}")
            own.append(PurchaseTicket.objects.create(user=self.user, amount=1000, title=f"Own {i}"))
        own_ids = sorted((t.pk for t in own), reverse=True)
        with patch.object(PurchaseTicket, "prefilter_visible", classmethod(prefilter_all)):
            for offset, expected_id in enumerate(own_ids):
                data = self.get(self.user, self.ticket_list_url, {"limit": 1, "offset": offset})
                self.assertEqual(3, data["count"])
                self.assertEqual([expected_id], [t["id"] for t in data["results"]])

    def test_ticket_update_list_count_excludes_hidden(self):
        self.ticket.comment(TicketUpdate(notes="Internal: password is hunter2", user=self.admin))
        with patch.object(TicketUpdate, "is_visible", visible_to_superusers):
            params = {"notes__icontains": "hunter2", "limit": 1}
            data = self.get(self.user, self.ticket_update_list_url, params)
            self.assertEqual(0, data["count"])
            self.assertEqual([], data["results"])
            data = self.get(self.admin, self.ticket_update_list_url, params)
            self.assertEqual(1, data["count"])
            self.assertEqual(1, len(data["results"]))

    def test_default_is_visible_paginates_in_database(self):
        """
        Without is_visible overrides, prefilter_visible is exact: no filtering in Python
        """
        with patch.object(PurchaseTicket, "is_visible", Ticket.is_visible), \
                patch.object(api, "paginate_visible", wraps=api.paginate_visible) as paginate_visible:
            data = self.get(self.user, self.ticket_list_url, {"limit": 1})
            self.assertEqual(1, data["count"])
            data = self.get(self.admin, self.ticket_update_list_url, {"limit": 1})
            self.assertEqual(0, data["count"])
        paginate_visible.assert_not_called()

    def test_ticket_update_list_loads_each_ticket_once(self):
        def count_queries(n_updates):
            for i in range(n_updates):
                self.ticket.comment(TicketUpdate(notes=f"Note {i}", user=self.user))
            with CaptureQueriesContext(connection) as queries:
                data = self.get(self.admin, self.ticket_update_list_url, {"limit": 1})
            self.assertEqual(TicketUpdate.objects.count(), data["count"])
            return len(queries)

        with patch.object(TicketUpdate, "is_visible", visible_to_superusers):
            self.assertEqual(count_queries(1), count_queries(5))

    def test_visible_pages_bounded_by_max_limit(self):
        for i in range(2):
            PurchaseTicket.objects.create(user=self.other_user, amount=1000, title=f"Other {i}")
            PurchaseTicket.objects.create(user=self.user, amount=1000, title=f"Own {i}")

        class Config:
            API_MAX_LIMIT = 2

        with patch.object(PurchaseTicket, "prefilter_visible", classmethod(prefilter_all)), \
                patch("django_fsm_ticket.api.fsm_tickets_config", Config):
            for params in ({}, {"limit": 100}):
                data = self.get(self.user, self.ticket_list_url, params)
                self.assertEqual(3, data["count"])
                self.assertEqual(2, len(data["results"]))
                self.assertTrue(all(t["user"] == self.user.pk for t in data["results"]))
