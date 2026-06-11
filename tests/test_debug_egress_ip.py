import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main


class DebugEgressIpEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_requires_cron_token(self):
        with patch.dict(os.environ, {"CRON_SERVICE_TRIGGER_TOKEN": "secret"}):
            with patch(
                "app.main._fetch_egress_ip",
                return_value={"ip": "203.0.113.10", "provider": "api.ipify.org"},
                create=True,
            ) as fetch_egress_ip:
                response = self.client.get("/debug/egress-ip")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "cron trigger token invalid"})
        fetch_egress_ip.assert_not_called()

    def test_returns_observed_egress_ip(self):
        with patch.dict(os.environ, {"CRON_SERVICE_TRIGGER_TOKEN": "secret"}):
            with patch(
                "app.main._fetch_egress_ip",
                return_value={"ip": "203.0.113.10", "provider": "api.ipify.org"},
                create=True,
            ) as fetch_egress_ip:
                response = self.client.get(
                    "/debug/egress-ip",
                    headers={"X-Cron-Token": "secret"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"ip": "203.0.113.10", "provider": "api.ipify.org"},
        )
        fetch_egress_ip.assert_called_once_with()


class FetchEgressIpTest(unittest.TestCase):
    def test_fetches_ip_from_provider(self):
        calls = []

        class FakeResponse:
            def raise_for_status(self):
                calls.append(("raise_for_status",))

            def json(self):
                calls.append(("json",))
                return {"ip": "203.0.113.10"}

        class FakeClient:
            def __init__(self, **kwargs):
                calls.append(("init", kwargs))

            def __enter__(self):
                calls.append(("enter",))
                return self

            def __exit__(self, exc_type, exc, traceback):
                calls.append(("exit", exc_type, exc, traceback))

            def get(self, url):
                calls.append(("get", url))
                return FakeResponse()

        with patch("app.main.httpx.Client", FakeClient):
            result = main._fetch_egress_ip()

        self.assertEqual(result, {"ip": "203.0.113.10", "provider": "api.ipify.org"})
        self.assertIn(("get", "https://api.ipify.org?format=json"), calls)
        self.assertIn(("raise_for_status",), calls)
        self.assertIn(("json",), calls)


if __name__ == "__main__":
    unittest.main()
