import json
import os
from pathlib import Path
import shutil
from rest_framework.test import APIClient

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings, tag
from django.urls import reverse

from django_fsm_ticket import api
from django_fsm_ticket.models import TicketUpdate

from tests.models import PurchaseTicket

TEST_DIR = BASE_DIR = os.path.join(Path(__file__).resolve().parent, "test_data")


@override_settings(
    MEDIA_ROOT=(TEST_DIR),
)
class TicketViewTest(TestCase):
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
        self.ticket = PurchaseTicket.objects.create(
            user=self.user,
            amount=1000,
            title="Test title",
            description="Some description",
        )

    def tearDown(self):
        try:
            shutil.rmtree(TEST_DIR)
        except OSError:
            pass
        return super().tearDown()

    @tag("list")
    def test_all_tickets(self):
        request = self.factory.get("/api/purchase")
        request.user = self.user
        response = api.ticket_list_factory(request, root_name="purchase")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(1, len(response.data))

    @tag("list", "order")
    def test_order_tickets(self):
        """
        List tickets does automatic ordering
        """
        PurchaseTicket.objects.create(
            user=self.user,
            amount=1000,
            title="AAAA title",
            description="Some description",
        )
        request = self.factory.get("/api/purchase")
        request.user = self.user
        # Default order shows last added first
        response = api.ticket_list_factory(request, root_name="purchase")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(2, len(response.data))
        self.assertEqual("AAAA title", response.data[0].get("title"))
        # Reverse order title should list by title in descending order
        request = self.factory.get("/api/purchase?ordering=-title")
        request.user = self.user
        response = api.ticket_list_factory(request, root_name="purchase")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(2, len(response.data))
        self.assertEqual("Test title", response.data[0].get("title"))
        self.assertEqual("New", response.data[0].get("state_verbose"))

    @tag("list")
    def test_all_tickets_paginated(self):
        request = self.factory.get("/api/purchase?limit=10")
        request.user = self.user
        response = api.ticket_list_factory(request, root_name="purchase")
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data.get("count"))

    @tag("list")
    def test_list_tickets(self):
        client = APIClient()
        client.force_login(user=self.user)
        response = client.get(
            reverse(
                "ticket-list",
                kwargs={
                    "root_name": "purchase",
                },
            ),
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(response.data))
        obj = response.data[0]
        self.assertEqual("Test title", obj.get("title"))

    @tag("list")
    def test_list_tickets_hide_closed(self):
        client = APIClient()
        client.force_login(user=self.user)
        list_url = reverse(
            "ticket-list",
            kwargs={
                "root_name": "purchase",
            },
        )
        response = client.get(f"{list_url}?hide_closed=false")
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(response.data))
        obj = response.data[0]
        self.assertEqual("Test title", obj.get("title"))
        self.ticket.state = "rejected"  # assign a closed state
        self.ticket.save()
        response = client.get(f"{list_url}?hide_closed=true")
        self.assertEqual(200, response.status_code)
        self.assertEqual(0, len(response.data))
        # default is to show closed
        response = client.get(list_url)
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(response.data))

    @tag("list")
    def test_list_tickets_admin(self):
        """Admin can see all tickets"""
        client = APIClient()
        client.force_login(user=self.admin)
        response = client.get(
            reverse(
                "ticket-list",
                kwargs={
                    "root_name": "purchase",
                },
            ),
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(response.data))
        obj = response.data[0]
        self.assertEqual("Test title", obj.get("title"))

    @tag("list")
    def test_list_tickets_another_user(self):
        """A user only sees her own tickets"""
        client = APIClient()
        another_user = User.objects.create_user(
            username="anotheruser", email="test@fub.it", password="top_secret"
        )
        client.force_login(user=another_user)
        response = client.get(
            reverse(
                "ticket-list",
                kwargs={
                    "root_name": "purchase",
                },
            ),
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(0, len(response.data))

    @tag("list")
    def test_list_tickets_not_allowed(self):
        """User must be logged in to see tickets"""
        client = APIClient()
        response = client.get(
            reverse(
                "ticket-list",
                kwargs={
                    "root_name": "purchase",
                },
            ),
        )
        self.assertEqual(403, response.status_code)

    @tag("list", "ticket_update")
    def test_list_ticket_updates(self):
        list_url = reverse(
            "ticket-update-list",
            kwargs={
                "root_name": "purchase",
            },
        )
        client = APIClient()
        client.force_login(user=self.user)
        response = client.get(list_url)
        self.assertEqual(200, response.status_code)
        self.assertEqual(0, len(response.data))
        # Make an update
        self.ticket.comment(TicketUpdate(notes="test notes"))
        response = client.get(list_url)
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(response.data))
        # Ticket update should include user and ticket data
        self.assertIsNotNone(response.data[0].get("ticket"), response.data)
        self.assertIsNotNone(response.data[0].get("ticket").get("id"), response.data[0])
        # User is none since called programmatically
        self.assertIsNone(response.data[0].get("user"), response.data)
        # Check that filtering is working
        response = client.get(f"{list_url}?search=blah")
        self.assertEqual(200, response.status_code)
        self.assertEqual(0, len(response.data))
        response = client.get(f"{list_url}?notes=blah")
        self.assertEqual(200, response.status_code)
        self.assertEqual(0, len(response.data))
        response = client.get(f"{list_url}?notes__icontains=test")
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(response.data))
        response = client.get(f"{list_url}?type=test")
        self.assertEqual(200, response.status_code)
        self.assertEqual(0, len(response.data))
        response = client.get(f"{list_url}?type=ticketupdate")
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(response.data))
        response = client.get(f"{list_url}?type__in=ticketupdate,test")
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(response.data))
        response = client.get(f"{list_url}?type__not=ticketupdate")
        self.assertEqual(200, response.status_code)
        self.assertEqual(0, len(response.data))

    @tag("list", "ticket_update")
    def test_list_ticket_updates_unknown_root(self):
        client = APIClient()
        client.force_login(user=self.user)
        response = client.get(
            reverse(
                "ticket-update-list",
                kwargs={
                    "root_name": "some-other-root",
                },
            ),
        )
        self.assertEqual(404, response.status_code)

    @tag("detail")
    def test_ticket_detail(self):
        client = APIClient()
        client.force_login(user=self.user)
        response = client.get(
            reverse(
                "ticket-detail",
                kwargs={"root_name": "purchase", "pk": self.ticket.id},
            ),
        )
        self.assertEqual(200, response.status_code)
        obj = response.data
        self.assertEqual("Test title", obj.get("title"))
        self.assertEqual(
            [{"name": "comment", "verbose_name": "Comment", "priority": 0.1}],
            obj.get("actions"),
        )

    @tag("deletion")
    def test_ticket_deletion_forbidden(self):
        """User cannot delete ticket"""
        client = APIClient()
        client.force_login(user=self.user)
        response = client.delete(
            reverse(
                "ticket-detail",
                kwargs={"root_name": "purchase", "pk": self.ticket.id},
            ),
        )
        self.assertEqual(403, response.status_code)

    @tag("detail")
    def test_ticket_detail_admin(self):
        """Admin can see ticket of another user with possible actions"""
        client = APIClient()
        client.force_login(user=self.admin)
        response = client.get(
            reverse(
                "ticket-detail",
                kwargs={"root_name": "purchase", "pk": self.ticket.id},
            ),
        )
        self.assertEqual(200, response.status_code)
        obj = response.data
        self.assertEqual("Test title", obj.get("title"))
        self.assertEqual(
            [
                {"name": "approve", "verbose_name": "Approve", "priority": 1.0},
                {"name": "comment", "verbose_name": "Comment", "priority": 0.1},
            ],
            obj.get("actions"),
            obj,
        )

    @tag("detail")
    def test_ticket_detail_anonymous_user(self):
        """User must be logged in to see tickets"""
        client = APIClient()
        response = client.get(
            reverse(
                "ticket-detail",
                kwargs={"root_name": "purchase", "pk": self.ticket.id},
            ),
        )
        self.assertEqual(403, response.status_code)

    @tag("detail")
    def test_ticket_not_visible(self):
        """User cannot see a ticket that belongs to another user"""
        another_user = User.objects.create_user(
            username="anotheruser", email="test@fub.it", password="top_secret"
        )
        client = APIClient()
        client.force_login(user=another_user)
        response = client.get(
            reverse(
                "ticket-detail",
                kwargs={"root_name": "purchase", "pk": self.ticket.id},
            ),
        )
        self.assertEqual(404, response.status_code)

    @tag("create")
    def test_create_ticket(self):
        """Create ticket via API"""
        client = APIClient()
        client.force_login(user=self.user)
        response = client.post(
            reverse(
                "ticket-list",
                kwargs={
                    "root_name": "purchase",
                },
            ),
            data=json.dumps({"title": "New title", "amount": 123}),
            content_type="application/json",
        )
        self.assertEqual(201, response.status_code, response.data)
        self.assertTrue("amount" in response.data)
        self.assertEqual(2, PurchaseTicket.objects.count())

    @tag("update")
    def test_update_ticket_data(self):
        """Update ticket"""
        client = APIClient()
        client.force_login(user=self.user)
        response = client.patch(
            reverse(
                "ticket-detail",
                kwargs={"root_name": "purchase", "pk": self.ticket.id},
            ),
            data=json.dumps({"title": "New title"}),
            content_type="application/json",
        )
        self.assertEqual(403, response.status_code, response.data)
        client.force_login(user=self.admin)
        response = client.patch(
            reverse(
                "ticket-detail",
                kwargs={"root_name": "purchase", "pk": self.ticket.id},
            ),
            data=json.dumps({"title": "New title"}),
            content_type="application/json",
        )
        self.assertEqual(200, response.status_code, response.data)
        self.ticket.refresh_from_db()
        self.assertEqual("New title", self.ticket.title)

    @tag("action", "ticket_update")
    def test_update_ticket_with_action(self):
        """Change ticket state"""
        client = APIClient()
        client.force_login(user=self.user)
        self.assertEqual(0, TicketUpdate.objects.filter(ticket=self.ticket).count())
        response = client.patch(
            reverse(
                "ticket-detail-action",
                kwargs={
                    "root_name": "purchase",
                    "pk": self.ticket.id,
                    "action_name": "comment",
                },
            ),
            data=json.dumps({"notes": "A note"}),
            content_type="application/json",
        )
        self.assertEqual(201, response.status_code, response.data)
        self.ticket.refresh_from_db()
        self.assertEqual(1, TicketUpdate.objects.filter(ticket=self.ticket).count())
        # Get ticket should include the update
        response = client.get(
            reverse(
                "ticket-detail",
                kwargs={"root_name": "purchase", "pk": self.ticket.id},
            ),
        )
        self.assertEqual(200, response.status_code)
        obj = response.data
        self.assertEqual(1, len(obj.get("updates")))
        update = obj.get("updates")[0]
        self.assertEqual("A note", update.get("notes"))
        self.assertEqual("testuser", update.get("user_verbose"), update)
        # Check that ticket update shows in the ticket update list
        response = client.get(
            reverse(
                "ticket-update-list",
                kwargs={"root_name": "purchase"},
            ),
        )
        self.assertEqual(200, response.status_code)
        ticket_update_id = response.data[0].get("id")
        response = client.get(
            reverse(
                "ticket-update-detail",
                kwargs={"root_name": "purchase", "pk": ticket_update_id},
            ),
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("A note", response.data.get("notes"))

    @tag("action")
    def test_update_ticket_unknown_action(self):
        """Handle unknown action"""
        client = APIClient()
        client.force_login(user=self.user)
        self.assertEqual(0, TicketUpdate.objects.filter(ticket=self.ticket).count())
        response = client.patch(
            reverse(
                "ticket-detail-action",
                kwargs={
                    "root_name": "purchase",
                    "pk": self.ticket.id,
                    "action_name": "some-unknown-action",
                },
            ),
            data=json.dumps({"notes": "A note"}),
            content_type="application/json",
        )
        self.assertEqual(404, response.status_code)

    @tag("action")
    def test_update_ticket_action_not_allowed(self):
        """Block unauthorized user from making action"""
        client = APIClient()
        client.force_login(user=self.user)
        self.assertEqual(0, TicketUpdate.objects.filter(ticket=self.ticket).count())
        response = client.patch(
            reverse(
                "ticket-detail-action",
                kwargs={
                    "root_name": "purchase",
                    "pk": self.ticket.id,
                    "action_name": "approve",
                },
            ),
            data=json.dumps({"notes": "A note"}),
            content_type="application/json",
        )
        self.assertEqual(403, response.status_code)

    @tag("action", "lifecycle")
    def test_update_ticket(self):
        """Admin completes the ticket lifecycle"""
        client = APIClient()
        client.force_login(user=self.admin)
        self.assertEqual(0, TicketUpdate.objects.filter(ticket=self.ticket).count())
        response = client.patch(
            reverse(
                "ticket-detail-action",
                kwargs={
                    "root_name": "purchase",
                    "pk": self.ticket.id,
                    "action_name": "approve",
                },
            ),
            data=json.dumps({"notes": "Approve note"}),
            content_type="application/json",
        )
        self.assertEqual(201, response.status_code, response.data)
        response = client.put(
            reverse(
                "ticket-detail-action",
                kwargs={
                    "root_name": "purchase",
                    "pk": self.ticket.id,
                    "action_name": "order",
                },
            ),
            data=json.dumps(
                {"notes": "Order note", "price": 10, "seller": "Some seller"}
            ),
            content_type="application/json",
        )
        self.assertEqual(201, response.status_code, response.data)

        tmp_file = SimpleUploadedFile(
            "file.jpg", b"file_content", content_type="image/jpg"
        )
        response = client.put(
            reverse(
                "ticket-detail-action",
                kwargs={
                    "root_name": "purchase",
                    "pk": self.ticket.id,
                    "action_name": "purchased",
                },
            ),
            {"notes": "Purchase note", "file": tmp_file},
            format="multipart",
        )
        self.assertEqual(201, response.status_code, response.data)

        self.ticket.refresh_from_db()
        self.assertEqual(3, TicketUpdate.objects.filter(ticket=self.ticket).count())
        # Get ticket should include the update
        response = client.get(
            reverse(
                "ticket-detail",
                kwargs={"root_name": "purchase", "pk": self.ticket.id},
            ),
        )
        self.assertEqual(200, response.status_code)
        obj = response.data
        self.assertEqual(3, len(obj.get("updates")))
        update = obj.get("updates")[0]
        self.assertEqual("Approve note", update.get("notes"))
        update = obj.get("updates")[2]
        self.assertEqual("Purchase note", update.get("notes"))
        self.assertIsNotNone(update.get("file"))
