from unittest import mock

from django.test import SimpleTestCase, override_settings
from django.urls import reverse


@override_settings(SECURE_SSL_REDIRECT=True, SECURE_HSTS_SECONDS=3600)
class HttpsInProductionTests(SimpleTestCase):
    url = reverse("public-config")

    def test_redirects_plain_http(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 301)
        self.assertTrue(response["Location"].startswith("https://"))

    def test_trusts_the_scheme_the_gateway_reports(self):
        response = self.client.get(self.url, HTTP_X_FORWARDED_PROTO="https")

        self.assertEqual(response.status_code, 200)
        self.assertIn("max-age=3600", response["Strict-Transport-Security"])

    @mock.patch("core.views.s3_client")
    @mock.patch("core.views.redis.Redis.from_url")
    @mock.patch("core.views.connection")
    def test_container_health_checks_stay_on_http(self, connection, redis_from_url, s3_client):
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 200)


@mock.patch("core.views.s3_client")
@mock.patch("core.views.redis.Redis.from_url")
@mock.patch("core.views.connection")
class HealthEndpointTests(SimpleTestCase):
    url = reverse("health")

    def test_returns_ok_when_all_dependencies_are_up(self, connection, redis_from_url, s3_client):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "checks": {"database": True, "redis": True, "storage": True}},
        )

    def test_returns_503_when_storage_is_down(self, connection, redis_from_url, s3_client):
        s3_client.return_value.head_bucket.side_effect = ConnectionError("bucket unreachable")

        with self.assertLogs("core.views", level="ERROR"):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "degraded")
        self.assertFalse(response.json()["checks"]["storage"])
        self.assertTrue(response.json()["checks"]["database"])

    def test_returns_503_when_database_is_down(self, connection, redis_from_url, s3_client):
        connection.ensure_connection.side_effect = ConnectionError("db unreachable")

        with self.assertLogs("core.views", level="ERROR"):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["checks"]["database"])

    def test_rejects_non_get_methods(self, connection, redis_from_url, s3_client):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 405)
