import sys
import types
import unittest
from unittest.mock import patch


google_module = types.ModuleType("google")
cloud_module = types.ModuleType("google.cloud")
storage_module = types.ModuleType("google.cloud.storage")
httpx_module = types.ModuleType("httpx")
cloud_module.storage = storage_module
google_module.cloud = cloud_module
storage_module.Blob = object
storage_module.Bucket = object
storage_module.Client = object
httpx_module.Client = object
httpx_module.Timeout = object
sys.modules.setdefault("google", google_module)
sys.modules.setdefault("google.cloud", cloud_module)
sys.modules.setdefault("google.cloud.storage", storage_module)
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

    def test_pop_polls_payload_keeps_existing_query_shape(self):
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
        self.assertEqual(calls[0]["take"], 1)
        self.assertEqual(calls[0]["skip"], 0)
        self.assertEqual(calls[0]["orderBy"], [{"totalVotes": "desc"}])
        self.assertIn("gt", calls[0]["where"]["expiresAt"])
        self.assertEqual(meta["pop_polls_take"], 1)


if __name__ == "__main__":
    unittest.main()
