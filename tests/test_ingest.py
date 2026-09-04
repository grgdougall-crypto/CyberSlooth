import unittest
from unittest.mock import Mock, patch

import app as cyberslooth


class FakeResponse:
    def __init__(self, *, status=200, headers=None, body=b"", encoding="utf-8"):
        self.status_code = status
        self.headers = headers or {}
        self.encoding = encoding
        self._body = body

    def iter_content(self, chunk_size=16384):
        for offset in range(0, len(self._body), chunk_size):
            yield self._body[offset:offset + chunk_size]

    def close(self):
        pass


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.client = cyberslooth.app.test_client()

    def test_rejects_malformed_and_extra_json(self):
        response = self.client.post("/api/ingest", json={"url": "not-a-url"})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/ingest", json={"url": "https://example.com", "extra": True})
        self.assertEqual(response.status_code, 400)

    def test_rejects_local_and_private_addresses(self):
        for url in ("http://localhost/", "http://127.0.0.1/", "http://10.0.0.2/", "http://[::1]/"):
            with self.subTest(url=url):
                response = self.client.post("/api/ingest", json={"url": url})
                self.assertEqual(response.status_code, 400)

    @patch.object(cyberslooth, "resolve_public_host", return_value=["93.184.216.34"])
    @patch.object(cyberslooth.requests, "Session")
    def test_mocked_public_html_returns_provenance(self, session_class, _resolver):
        response = FakeResponse(
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<title>Field Notes</title><main>Hello archive</main><a href='/lead'>Lead</a>",
        )
        session = Mock()
        session.get.return_value = response
        session.headers = {}
        session_class.return_value = session

        result = self.client.post("/api/ingest", json={"url": "https://example.com/page"})
        payload = result.get_json()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(payload["evidence"]["source"]["requested_url"], "https://example.com/page")
        self.assertEqual(payload["evidence"]["content"]["title"], "Field Notes")
        self.assertEqual(payload["evidence"]["links"]["candidates"], ["https://example.com/lead"])
        self.assertFalse(payload["evidence"]["analysis"]["performed"])

    @patch.object(cyberslooth, "resolve_public_host", return_value=["93.184.216.34"])
    @patch.object(cyberslooth.requests, "Session")
    def test_redirect_to_private_destination_is_rejected(self, session_class, _resolver):
        session = Mock()
        session.get.return_value = FakeResponse(status=302, headers={"Location": "http://127.0.0.1/private"})
        session.headers = {}
        session_class.return_value = session
        response = self.client.post("/api/ingest", json={"url": "https://example.com/redirect"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(session.get.call_count, 1)

    @patch.object(cyberslooth, "resolve_public_host", return_value=["93.184.216.34"])
    @patch.object(cyberslooth.requests, "Session")
    def test_rejects_declared_oversized_response(self, session_class, _resolver):
        session = Mock()
        session.get.return_value = FakeResponse(headers={"Content-Type": "text/html", "Content-Length": str(cyberslooth.MAX_RESPONSE_BYTES + 1)})
        session.headers = {}
        session_class.return_value = session
        response = self.client.post("/api/ingest", json={"url": "https://example.com/large"})
        self.assertEqual(response.status_code, 413)

    @patch.object(cyberslooth, "resolve_public_host", return_value=["93.184.216.34"])
    @patch.object(cyberslooth.requests, "Session")
    def test_rejects_disallowed_content_type(self, session_class, _resolver):
        session = Mock()
        session.get.return_value = FakeResponse(headers={"Content-Type": "application/pdf"}, body=b"%PDF")
        session.headers = {}
        session_class.return_value = session
        response = self.client.post("/api/ingest", json={"url": "https://example.com/file.pdf"})
        self.assertEqual(response.status_code, 415)


if __name__ == "__main__":
    unittest.main()
