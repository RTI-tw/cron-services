import sys
import types
import unittest
from unittest.mock import patch


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

from app import export_home_sections  # noqa: E402


class BuildHomePayloadsTest(unittest.TestCase):
    def test_fetches_all_active_editor_choices(self):
        calls = []

        def fake_execute_gql(query, variables=None):
            calls.append(variables)
            if variables["skip"] == 0:
                return {
                    "editorChoices": [{"id": "pick-1"}, {"id": "pick-2"}],
                    "editorChoicesCount": 3,
                }
            return {
                "editorChoices": [{"id": "pick-3"}],
                "editorChoicesCount": 3,
            }

        with patch.object(export_home_sections, "execute_gql", fake_execute_gql):
            payloads, meta = export_home_sections._build_home_payloads(
                include={"editor-choices"}
            )

        self.assertEqual(
            payloads["editor-choices.json"],
            {
                "editorChoices": [
                    {"id": "pick-1"},
                    {"id": "pick-2"},
                    {"id": "pick-3"},
                ],
                "editorChoicesCount": 3,
            },
        )
        self.assertEqual(
            calls,
            [
                {
                    "where": {"state": {"equals": "active"}},
                    "orderBy": [{"sortOrder": "asc"}],
                    "take": 100,
                    "skip": 0,
                },
                {
                    "where": {"state": {"equals": "active"}},
                    "orderBy": [{"sortOrder": "asc"}],
                    "take": 100,
                    "skip": 2,
                },
            ],
        )
        self.assertEqual(meta["editor_choices_filter"], "state=active")
        self.assertEqual(meta["editor_choices_page_size"], 100)

    def test_pop_polls_payload_filters_ongoing_polls_and_orders_by_voter_count(self):
        calls = []
        expected_payload = {
            "polls": [{"id": "poll-1"}],
            "pollsCount": 1,
        }

        def fake_execute_gql(query, variables=None):
            calls.append(variables)
            return expected_payload

        with patch.object(export_home_sections, "execute_gql", fake_execute_gql):
            payloads, meta = export_home_sections._build_home_payloads(
                include={"pop-polls"}
            )

        self.assertEqual(payloads["pop-polls.json"], expected_payload)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["take"], 3)
        self.assertEqual(calls[0]["skip"], 0)
        self.assertEqual(calls[0]["orderBy"], [{"voterCount": "desc"}])
        self.assertIn("OR", calls[0]["where"])
        future_cutoff = calls[0]["where"]["OR"][1]["expiresAt"]["gt"]
        self.assertEqual(
            calls[0]["where"],
            {
                "OR": [
                    {"expiresAt": {"equals": None}},
                    {"expiresAt": {"gt": future_cutoff}},
                ]
            },
        )
        self.assertEqual(meta["pop_polls_take"], 3)


if __name__ == "__main__":
    unittest.main()
