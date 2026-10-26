import importlib
import warnings

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from django.urls import clear_url_caches

from django_fsm_ticket import urls


API_URL_NAMES = {
    "ticket-list",
    "ticket-update-list",
    "ticket-update-detail",
    "ticket-detail",
    "ticket-detail-action",
}


class ApiPrefixSettingTest(SimpleTestCase):
    """
    FSM_TICKET_API_PREFIX is read when the URLconf module is imported,
    so each test reloads `django_fsm_ticket.urls` with the setting under test
    """

    def tearDown(self):
        # Restore the URLconf built from the test settings
        importlib.reload(urls)
        clear_url_caches()

    def reload_urls(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.reload(urls)
        clear_url_caches()
        names = {p.name for p in urls.urlpatterns}
        return names & API_URL_NAMES, caught

    def test_prefix_unset_enables_api_and_warns(self):
        with self.settings():
            del settings.FSM_TICKET_API_PREFIX
            api_names, caught = self.reload_urls()
        self.assertEqual(API_URL_NAMES, api_names)
        self.assertEqual(1, len(caught))
        self.assertIs(FutureWarning, caught[0].category)
        self.assertIn("FSM_TICKET_API_PREFIX", str(caught[0].message))

    @override_settings(FSM_TICKET_API_PREFIX=None)
    def test_prefix_none_disables_api(self):
        api_names, caught = self.reload_urls()
        self.assertEqual(set(), api_names)
        self.assertEqual([], caught)

    @override_settings(FSM_TICKET_API_PREFIX="custom-api")
    def test_explicit_prefix_enables_api_without_warning(self):
        api_names, caught = self.reload_urls()
        self.assertEqual(API_URL_NAMES, api_names)
        self.assertEqual([], caught)
        self.assertTrue(
            all(
                str(p.pattern).startswith("custom-api/")
                for p in urls.urlpatterns
                if p.name in API_URL_NAMES
            )
        )

    @override_settings(FSM_TICKET_API_PREFIX="/custom-api/")
    def test_prefix_slashes_are_stripped(self):
        api_names, caught = self.reload_urls()
        self.assertEqual(API_URL_NAMES, api_names)
        self.assertEqual([], caught)
        self.assertTrue(
            all(
                str(p.pattern).startswith("custom-api/<")
                for p in urls.urlpatterns
                if p.name in API_URL_NAMES
            )
        )

    def test_non_string_prefix_is_rejected(self):
        for prefix in [False, 0, ["api"]]:
            with self.subTest(prefix=prefix), self.settings(FSM_TICKET_API_PREFIX=prefix):
                with self.assertRaises(ImproperlyConfigured):
                    importlib.reload(urls)

    def test_empty_prefix_is_rejected(self):
        for prefix in ["", "/"]:
            with self.subTest(prefix=prefix), self.settings(FSM_TICKET_API_PREFIX=prefix):
                with self.assertRaises(ImproperlyConfigured):
                    importlib.reload(urls)
