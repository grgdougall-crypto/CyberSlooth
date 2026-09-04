import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app as cyberslooth


CANDIDATE_A = "https://example.org/lead-a"
CANDIDATE_B = "https://example.org/lead-b"
CHILD_LEAD = "https://example.org/child"


def evidence_record(candidates=None):
    candidates = [CANDIDATE_A, CANDIDATE_B] if candidates is None else candidates
    return {
        "source": {
            "requested_url": "https://example.com/",
            "final_url": "https://example.com/",
            "retrieved_at": "2026-09-03T20:00:00Z",
            "status_code": 200,
            "content_type": "text/html",
        },
        "content": {"title": "Starting page", "text_excerpt": "A starting research lead.", "text_length": 25},
        "links": {"found": len(candidates), "candidates": candidates, "followed": False},
        "analysis": {"performed": False, "label": "No AI analysis has been performed yet."},
    }


def valid_analysis(candidates=None):
    candidates = [CANDIDATE_A, CANDIDATE_B] if candidates is None else candidates
    return {
        "summary": "A bounded starting-point summary.",
        "page_type": "Research lead",
        "why_interesting": "It contains candidate references worth bounded inspection.",
        "observations": [{"claim": "Candidate references are present.", "evidence": "Two links were extracted."}],
        "uncertainties": ["The linked pages have not yet been inspected."],
        "candidate_follow_ups": [{"url": url, "reason": "Potential corroborating source."} for url in candidates],
        "archive_recommendation": {"decision": "review", "reason": "A useful starting point."},
        "confidence": "medium",
    }


def model_response(value):
    return SimpleNamespace(status="completed", output_text=json.dumps(value), output=[])


def follow_up_analysis():
    return {
        "summary": "The follow-up adds a bounded detail.",
        "page_type": "Reference page",
        "why_interesting": "It gives direct supporting context.",
        "observations": [{"claim": "A detail is stated.", "evidence": "The excerpt contains the detail."}],
        "uncertainties": ["The wider historical context remains uncertain."],
        "candidate_follow_ups": [{"url": CHILD_LEAD, "reason": "A later possible lead."}],
        "archive_recommendation": {"decision": "keep", "reason": "It adds evidence."},
        "confidence": "high",
    }


def fetched(url):
    return {
        "requested_url": url,
        "final_url": url,
        "status_code": 200,
        "content_type": "text/html",
        "retrieved_at": "2026-09-03T21:00:00Z",
        "body": f"<title>{url.rsplit('/', 1)[-1]}</title><main>Follow-up detail.</main><a href='{CHILD_LEAD}'>Child</a>",
    }


def synthesis(urls, best_next=CHILD_LEAD):
    return {
        "starting_point": "The original page supplied a lead but left its references untested.",
        "explored": [
            {
                "url": url,
                "title": "provider title is replaced",
                "selection_reason": "provider reason is replaced",
                "summary": "The follow-up adds a bounded detail.",
                "what_it_added": "It provided direct context not present in the starting excerpt.",
                "confidence": "high",
            }
            for url in urls
        ],
        "synthesis": {
            "what_changed": "The lead now has limited supporting context.",
            "what_was_confirmed": ["A related reference exists."],
            "what_remains_uncertain": ["Broader context remains unverified."],
            "best_next_lead": {"url": best_next, "reason": "Suggested for a future, separate expedition."},
            "research_value": "medium",
        },
    }


class ExplorationTests(unittest.TestCase):
    def setUp(self):
        self.client = cyberslooth.app.test_client()

    def test_rejects_missing_original_evidence(self):
        response = self.client.post("/api/explore", json={"analysis": valid_analysis()})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "missing_original_evidence")

    def test_rejects_missing_original_analysis(self):
        response = self.client.post("/api/explore", json={"evidence": evidence_record()})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "missing_original_analysis")

    def test_maximum_two_selected_links_is_enforced(self):
        candidates = [CANDIDATE_A, CANDIDATE_B, "https://example.org/lead-c"]
        provider = Mock()
        provider.responses.create.return_value = model_response({
            "selected": [{"url": url, "reason": "ranked"} for url in candidates]
        })
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=True), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ):
            response = self.client.post("/api/explore", json={
                "evidence": evidence_record(candidates), "analysis": valid_analysis(candidates)
            })
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"]["code"], "selection_limit")

    def test_invented_selected_url_is_rejected(self):
        provider = Mock()
        provider.responses.create.return_value = model_response({
            "selected": [{"url": "https://invented.example/", "reason": "invented"}]
        })
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=True), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ):
            response = self.client.post("/api/explore", json={"evidence": evidence_record(), "analysis": valid_analysis()})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"]["code"], "invented_candidate_url")

    def test_existing_candidate_survives_selection_validation(self):
        provider = Mock()
        provider.responses.create.return_value = model_response({"selected": [{"url": CANDIDATE_A, "reason": "ranked"}]})
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=True), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ):
            selected = cyberslooth.select_follow_up_links(
                evidence_record(), valid_analysis(), cyberslooth.ModelCallBudget()
            )
        self.assertEqual(selected[0]["url"], CANDIDATE_A)

    def test_private_host_protection_applies_to_follow_up(self):
        private = "https://private.example/lead"
        provider = Mock()
        provider.responses.create.return_value = model_response({"selected": [{"url": private, "reason": "ranked"}]})
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=True), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ), patch.object(
            cyberslooth, "resolve_public_host",
            side_effect=cyberslooth.IngestError("unsafe_address", "The source resolves to a non-public network address."),
        ):
            response = self.client.post("/api/explore", json={
                "evidence": evidence_record([private]), "analysis": valid_analysis([private])
            })
        self.assertEqual(response.status_code, 502)
        item = response.get_json()["expedition"]["explored"][0]
        self.assertEqual(item["retrieval"]["error"]["code"], "unsafe_address")

    def test_one_success_and_one_failure_returns_partial_result(self):
        provider = Mock()
        provider.responses.create.side_effect = [
            model_response({"selected": [{"url": CANDIDATE_A, "reason": "first"}, {"url": CANDIDATE_B, "reason": "second"}]}),
            model_response(follow_up_analysis()),
            model_response(synthesis([CANDIDATE_A])),
        ]

        def fetch_side_effect(url):
            if url == CANDIDATE_B:
                raise cyberslooth.IngestError("source_timeout", "Timed out.", 504)
            return fetched(url)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=True), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ), patch.object(cyberslooth, "fetch_public_page", side_effect=fetch_side_effect):
            response = self.client.post("/api/explore", json={"evidence": evidence_record(), "analysis": valid_analysis()})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["expedition"]
        self.assertEqual([item["retrieval"]["status"] for item in payload["explored"]], ["success", "failed"])
        self.assertIsNotNone(payload["explored"][0]["evidence"])
        self.assertEqual(payload["model_calls"]["used"], 3)

    def test_both_failed_follow_ups_fail_cleanly(self):
        provider = Mock()
        provider.responses.create.return_value = model_response({
            "selected": [{"url": CANDIDATE_A, "reason": "first"}, {"url": CANDIDATE_B, "reason": "second"}]
        })
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=True), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ), patch.object(
            cyberslooth, "fetch_public_page", side_effect=cyberslooth.IngestError("source_unavailable", "Unavailable.", 502)
        ):
            response = self.client.post("/api/explore", json={"evidence": evidence_record(), "analysis": valid_analysis()})
        self.assertEqual(response.status_code, 502)
        payload = response.get_json()["expedition"]
        self.assertEqual(payload["failure"]["code"], "follow_ups_failed")
        self.assertIsNone(payload["synthesis"])

    def test_retrieved_evidence_survives_follow_up_analysis_failure(self):
        provider = Mock()
        provider.responses.create.side_effect = [
            model_response({"selected": [{"url": CANDIDATE_A, "reason": "first"}]}),
            model_response({"summary": "incomplete"}),
        ]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=True), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ), patch.object(cyberslooth, "fetch_public_page", return_value=fetched(CANDIDATE_A)):
            response = self.client.post("/api/explore", json={"evidence": evidence_record(), "analysis": valid_analysis()})
        self.assertEqual(response.status_code, 502)
        item = response.get_json()["expedition"]["explored"][0]
        self.assertEqual(item["retrieval"]["status"], "success")
        self.assertEqual(item["analysis_status"]["status"], "failed")
        self.assertIsNotNone(item["evidence"])

    def test_follow_up_candidates_are_not_recursively_fetched(self):
        provider = Mock()
        provider.responses.create.side_effect = [
            model_response({"selected": [{"url": CANDIDATE_A, "reason": "first"}, {"url": CANDIDATE_B, "reason": "second"}]}),
            model_response(follow_up_analysis()),
            model_response(follow_up_analysis()),
            model_response(synthesis([CANDIDATE_A, CANDIDATE_B])),
        ]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=True), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ), patch.object(cyberslooth, "fetch_public_page", side_effect=lambda url: fetched(url)) as fetch_mock:
            response = self.client.post("/api/explore", json={"evidence": evidence_record(), "analysis": valid_analysis()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(fetch_mock.call_count, 2)
        self.assertEqual(provider.responses.create.call_count, cyberslooth.MAX_EXPLORE_MODEL_CALLS)
        self.assertEqual(response.get_json()["expedition"]["model_calls"]["used"], cyberslooth.MAX_EXPLORE_MODEL_CALLS)
        fetch_mock.assert_any_call(CANDIDATE_A)
        fetch_mock.assert_any_call(CANDIDATE_B)

    def test_best_next_lead_is_suggested_but_not_fetched(self):
        provider = Mock()
        provider.responses.create.side_effect = [
            model_response({"selected": [{"url": CANDIDATE_A, "reason": "first"}]}),
            model_response(follow_up_analysis()),
            model_response(synthesis([CANDIDATE_A], best_next=CHILD_LEAD)),
        ]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=True), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ), patch.object(cyberslooth, "fetch_public_page", side_effect=lambda url: fetched(url)) as fetch_mock:
            response = self.client.post("/api/explore", json={"evidence": evidence_record(), "analysis": valid_analysis()})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["expedition"]
        self.assertEqual(payload["synthesis"]["best_next_lead"]["url"], CHILD_LEAD)
        fetch_mock.assert_called_once_with(CANDIDATE_A)

    def test_model_call_budget_is_a_hard_limit(self):
        budget = cyberslooth.ModelCallBudget()
        for _ in range(cyberslooth.MAX_EXPLORE_MODEL_CALLS):
            budget.consume()
        with self.assertRaises(cyberslooth.ExplorationError) as captured:
            budget.consume()
        self.assertEqual(captured.exception.code, "model_call_budget")


if __name__ == "__main__":
    unittest.main()
