import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import openai

import app as cyberslooth


def evidence_record():
    return {
        "source": {
            "requested_url": "https://example.com/",
            "final_url": "https://example.com/",
            "retrieved_at": "2026-09-03T20:00:00Z",
            "status_code": 200,
            "content_type": "text/html",
        },
        "content": {
            "title": "Example Domain",
            "text_excerpt": "Example Domain is used in documentation examples.",
            "text_length": 49,
        },
        "links": {
            "found": 1,
            "candidates": ["https://iana.org/domains/example"],
            "followed": False,
        },
        "analysis": {
            "performed": False,
            "label": "No AI analysis has been performed yet.",
        },
    }


def valid_analysis(candidate_url="https://iana.org/domains/example"):
    follow_ups = [] if candidate_url is None else [
        {"url": candidate_url, "reason": "The page names this as a related reference."}
    ]
    return {
        "summary": "A short informational example page.",
        "page_type": "Documentation example",
        "why_interesting": "It is a deliberately stable artifact designed for examples.",
        "observations": [
            {"claim": "The page identifies itself as an example domain.", "evidence": "Title: Example Domain."}
        ],
        "uncertainties": ["The evidence does not establish the page's publication history."],
        "candidate_follow_ups": follow_ups,
        "archive_recommendation": {"decision": "review", "reason": "Useful as a baseline retrieval record."},
        "confidence": "high",
    }


def mocked_client(output):
    response = SimpleNamespace(status="completed", output_text=json.dumps(output), output=[])
    client = Mock()
    client.responses.create.return_value = response
    return client


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.client = cyberslooth.app.test_client()

    def test_rejects_missing_evidence(self):
        response = self.client.post("/api/analyze", json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "missing_evidence")

    def test_missing_api_key_fails_safely(self):
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post("/api/analyze", json={"evidence": evidence_record()})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error"]["code"], "analysis_unavailable")

    def test_valid_mocked_structured_response_and_candidate_pass(self):
        provider = mocked_client(valid_analysis())
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ):
            response = self.client.post("/api/analyze", json={"evidence": evidence_record()})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["analysis"]["candidate_follow_ups"][0]["url"], "https://iana.org/domains/example")
        call = provider.responses.create.call_args.kwargs
        self.assertEqual(call["tools"], [])
        self.assertTrue(call["text"]["format"]["strict"])

    def test_invalid_schema_fails_safely(self):
        invalid = valid_analysis()
        del invalid["confidence"]
        provider = mocked_client(invalid)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ):
            response = self.client.post("/api/analyze", json={"evidence": evidence_record()})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"]["code"], "invalid_model_output")

    def test_invented_candidate_url_is_rejected(self):
        provider = mocked_client(valid_analysis("https://invented.example/"))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ):
            response = self.client.post("/api/analyze", json={"evidence": evidence_record()})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"]["code"], "invented_candidate_url")

    def test_provider_auth_error_never_exposes_secret(self):
        secret = "TEST_SECRET_VALUE_DO_NOT_EXPOSE"
        provider = Mock()
        provider_response = Mock(status_code=401, headers={})
        provider.responses.create.side_effect = openai.AuthenticationError(
            "Authentication failed for " + secret,
            response=provider_response,
            body={"error": "bad key"},
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": secret}, clear=True), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ), self.assertLogs(cyberslooth.app.logger, level="WARNING") as captured:
            response = self.client.post("/api/analyze", json={"evidence": evidence_record()})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(secret, response.get_data(as_text=True))
        self.assertNotIn(secret, " ".join(captured.output))


if __name__ == "__main__":
    unittest.main()
