"""Tests for the backend health-check endpoint."""

import unittest

from fastapi.testclient import TestClient

from backend.app.main import app


class HealthCheckTests(unittest.TestCase):
    def test_health_check_returns_ok(self) -> None:
        response = TestClient(app).get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
