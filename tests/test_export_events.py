import sys
import types
import unittest


google_module = types.ModuleType("google")
auth_module = types.ModuleType("google.auth")
auth_transport_module = types.ModuleType("google.auth.transport")
auth_requests_module = types.ModuleType("google.auth.transport.requests")
cloud_module = types.ModuleType("google.cloud")
oauth2_module = types.ModuleType("google.oauth2")
oauth2_id_token_module = types.ModuleType("google.oauth2.id_token")
storage_module = types.ModuleType("google.cloud.storage")
httpx_module = types.ModuleType("httpx")
auth_module.transport = auth_transport_module
auth_transport_module.requests = auth_requests_module
cloud_module.storage = storage_module
google_module.cloud = cloud_module
google_module.auth = auth_module
google_module.oauth2 = oauth2_module
oauth2_module.id_token = oauth2_id_token_module
auth_requests_module.Request = object
oauth2_id_token_module.fetch_id_token = lambda request, audience: ""
storage_module.Blob = object
storage_module.Bucket = object
storage_module.Client = object
httpx_module.Client = object
httpx_module.Timeout = object
sys.modules.setdefault("google", google_module)
sys.modules.setdefault("google.auth", auth_module)
sys.modules.setdefault("google.auth.transport", auth_transport_module)
sys.modules.setdefault("google.auth.transport.requests", auth_requests_module)
sys.modules.setdefault("google.cloud", cloud_module)
sys.modules.setdefault("google.cloud.storage", storage_module)
sys.modules.setdefault("google.oauth2", oauth2_module)
sys.modules.setdefault("google.oauth2.id_token", oauth2_id_token_module)
sys.modules.setdefault("httpx", httpx_module)

from app import export_events  # noqa: E402


class BuildEventsPayloadTest(unittest.TestCase):
    def test_keeps_three_sections_and_counts_across_them(self):
        payload = export_events._build_events_payload(
            {
                "hot": [{"id": "1"}, {"id": "2"}],
                "more": [{"id": "3"}],
                "past": [],
            },
            "2026-07-09T00:00:00+00:00",
        )

        self.assertEqual(payload["generatedAt"], "2026-07-09T00:00:00+00:00")
        self.assertEqual(payload["hot"], [{"id": "1"}, {"id": "2"}])
        self.assertEqual(payload["more"], [{"id": "3"}])
        self.assertEqual(payload["past"], [])
        self.assertEqual(payload["eventsCount"], 3)

    def test_missing_or_null_sections_become_empty_lists(self):
        payload = export_events._build_events_payload({"hot": None}, "now")

        self.assertEqual(payload["hot"], [])
        self.assertEqual(payload["more"], [])
        self.assertEqual(payload["past"], [])
        self.assertEqual(payload["eventsCount"], 0)


class ExportQueryTest(unittest.TestCase):
    """
    The export deliberately omits two fields. Both would be silently wrong in a
    static file, and both are easy to add back by reflex when someone copies the
    frontend's fragment, so pin them here.
    """

    def test_does_not_export_derived_or_per_member_fields(self):
        # availabilityStatus is computed from `now`; freezing it at export time
        # would keep a closed event advertising 立即報名 until the next run.
        self.assertNotIn("availabilityStatus", export_events.QUERY_EVENT_PREVIEWS)
        # isRegistered depends on who is asking; the exporter has no member.
        self.assertNotIn("isRegistered", export_events.QUERY_EVENT_PREVIEWS)

    def test_exports_everything_the_frontend_needs_to_derive_status(self):
        for field in (
            "startAt",
            "endAt",
            "registrationStartAt",
            "registrationEndAt",
            "capacity",
            "registrationCount",
        ):
            self.assertIn(field, export_events.QUERY_EVENT_PREVIEWS, field)

    def test_exports_the_fields_the_card_renders(self):
        for field in ("slug", "title_zh", "title_th", "firstImage"):
            self.assertIn(field, export_events.QUERY_EVENT_PREVIEWS, field)


if __name__ == "__main__":
    unittest.main()
