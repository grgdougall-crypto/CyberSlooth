import copy
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import openai
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

import app as cyberslooth
import archive_store
from test_explore import evidence_record, valid_analysis


SCORE_FIELDS = {
    "research_value_score": 3,
    "evidence_quality_score": 3,
    "interestingness_score": 3,
    "uncertainty_penalty": 1,
    "archive_quality_score": 3,
}


def scoring_output(public_ids, overrides=None, selected=None):
    overrides = overrides or {}
    candidates = []
    for public_id in public_ids:
        candidate = {
            "public_id": public_id,
            **SCORE_FIELDS,
            "total_score": 0,
            "reason": f"Concise reason for {public_id}.",
        }
        candidate.update(overrides.get(public_id, {}))
        candidates.append(candidate)
    return {
        "candidates": candidates,
        "selected_public_id": selected or public_ids[0],
        "selection_reason": "The strongest bounded candidate.",
    }


class DailyCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        database_path = (Path(self.temp.name) / "daily-test.db").as_posix()
        archive_store.configure_database("sqlite:///" + database_path)
        self.client = cyberslooth.app.test_client()

    def tearDown(self):
        archive_store.configure_database("sqlite:///" + archive_store.LOCAL_DATABASE_PATH.as_posix())
        self.temp.cleanup()

    def archive_many(self, count):
        public_ids = []
        for index in range(count):
            evidence = copy.deepcopy(evidence_record())
            analysis = copy.deepcopy(valid_analysis())
            evidence["content"]["title"] = f"Archived discovery {index:02d}"
            analysis["summary"] = f"Distinct stored summary {index:02d}."
            response = self.client.post("/api/archive", json={"evidence": evidence, "analysis": analysis})
            self.assertEqual(response.status_code, 201)
            public_ids.append(response.get_json()["public_id"])
        return public_ids

    def archive_url(self, url, title):
        evidence = copy.deepcopy(evidence_record())
        analysis = copy.deepcopy(valid_analysis())
        evidence["source"]["requested_url"] = url
        evidence["source"]["final_url"] = url
        evidence["content"]["title"] = title
        analysis["summary"] = f"Stored summary for {title}."
        storage, fingerprint = cyberslooth.validate_archive_payload({"evidence": evidence, "analysis": analysis})
        return archive_store.create_research_run(storage, fingerprint)[0]

    def provider_for(self, output):
        provider = MagicMock()
        provider.responses.create.return_value = SimpleNamespace(
            status="completed", output_text=json.dumps(output), output=[]
        )
        return provider

    def select_with(self, output):
        provider = self.provider_for(output)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ):
            response = self.client.post("/api/select-daily-candidate")
        return response, provider

    def test_rejects_selection_with_fewer_than_two_records(self):
        self.archive_many(1)
        response = self.client.post("/api/select-daily-candidate")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"]["code"], "insufficient_archive")

    def test_only_most_recent_ten_records_are_considered(self):
        all_ids = self.archive_many(11)
        recent = archive_store.list_recent_research_runs()
        output = scoring_output([record.public_id for record in recent])
        response, provider = self.select_with(output)
        self.assertEqual(response.status_code, 200)
        model_input = json.loads(provider.responses.create.call_args.kwargs["input"])
        supplied_ids = [record["public_id"] for record in model_input["records"]]
        self.assertEqual(len(supplied_ids), 10)
        self.assertNotIn(all_ids[0], supplied_ids)

    def test_valid_structured_scoring_response_passes(self):
        ids = self.archive_many(2)
        response, _ = self.select_with(scoring_output(list(reversed(ids))))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(len(response.get_json()["ranked"]), 2)

    def test_invented_public_id_is_rejected(self):
        ids = [record.public_id for record in self._recent_two()]
        output = scoring_output(ids)
        output["candidates"][1]["public_id"] = "CS-20990101-FFFFFF"
        response, _ = self.select_with(output)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"]["code"], "invented_public_id")

    def test_out_of_range_score_is_rejected(self):
        ids = [record.public_id for record in self._recent_two()]
        output = scoring_output(ids, {ids[0]: {"evidence_quality_score": 6}})
        response, _ = self.select_with(output)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"]["code"], "score_out_of_range")

    def test_total_score_is_recomputed_server_side(self):
        ids = [record.public_id for record in self._recent_two()]
        response, _ = self.select_with(scoring_output(ids))
        self.assertEqual(response.get_json()["ranked"][0]["total_score"], 16)
        self.assertEqual(response.get_json()["ranked"][0]["novelty_score"], 5)

    def test_tie_break_prefers_evidence_then_research_value(self):
        records = self._recent_two()
        newer, older = records[0].public_id, records[1].public_id
        overrides = {
            newer: {"research_value_score": 4, "evidence_quality_score": 3, "interestingness_score": 0, "archive_quality_score": 3, "uncertainty_penalty": 0},
            older: {"research_value_score": 1, "evidence_quality_score": 5, "interestingness_score": 0, "archive_quality_score": 4, "uncertainty_penalty": 0},
        }
        response, _ = self.select_with(scoring_output([newer, older], overrides))
        self.assertEqual(response.get_json()["selected_public_id"], older)

    def test_tie_break_prefers_newer_archive_last(self):
        records = self._recent_two()
        response, _ = self.select_with(scoring_output([record.public_id for record in records]))
        self.assertEqual(response.get_json()["selected_public_id"], records[0].public_id)

    def test_exactly_one_record_is_marked_selected(self):
        ids = [record.public_id for record in self._recent_three()]
        self.select_with(scoring_output(ids))
        selected = [record for record in archive_store.list_research_runs() if record.daily_candidate_selected]
        self.assertEqual(len(selected), 1)

    def test_existing_selection_survives_failed_reevaluation(self):
        ids = [record.public_id for record in self._recent_two()]
        first, _ = self.select_with(scoring_output(ids))
        selected_before = first.get_json()["selected_public_id"]
        invalid = scoring_output(ids, {ids[0]: {"archive_quality_score": 9}})
        response, _ = self.select_with(invalid)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(archive_store.get_current_daily_candidate().public_id, selected_before)

    def test_ranked_results_are_persisted_correctly(self):
        ids = [record.public_id for record in self._recent_three()]
        overrides = {ids[0]: {"research_value_score": 5}, ids[1]: {"research_value_score": 4}, ids[2]: {"research_value_score": 1}}
        response, _ = self.select_with(scoring_output(ids, overrides))
        ranked = response.get_json()["ranked"]
        persisted = archive_store.list_current_daily_ranking()
        self.assertEqual([record.public_id for record in persisted], [item["public_id"] for item in ranked])
        self.assertEqual([record.daily_candidate_rank for record in persisted], [1, 2, 3])
        self.assertEqual([record.daily_candidate_score for record in persisted], [item["total_score"] for item in ranked])

    def test_archive_page_shows_selected_candidate(self):
        ids = [record.public_id for record in self._recent_two()]
        selected_id = self.select_with(scoring_output(ids))[0].get_json()["selected_public_id"]
        html = self.client.get("/archive").get_data(as_text=True)
        self.assertIn("Daily Discovery Candidate", html)
        self.assertIn(selected_id, html)

    def test_archive_detail_shows_daily_candidate_badge(self):
        ids = [record.public_id for record in self._recent_two()]
        selected_id = self.select_with(scoring_output(ids))[0].get_json()["selected_public_id"]
        html = self.client.get(f"/archive/{selected_id}").get_data(as_text=True)
        self.assertIn("DAILY CANDIDATE", html)

    def test_no_secrets_or_raw_provider_payloads_are_persisted(self):
        ids = [record.public_id for record in self._recent_two()]
        output = scoring_output(ids)
        output["selection_reason"] = "SECRET_PROVIDER_PAYLOAD"
        self.select_with(output)
        stored = archive_store.list_research_runs()
        serialized = json.dumps([
            {
                "evidence": record.original_evidence_json,
                "analysis": record.original_analysis_json,
                "exploration": record.exploration_json,
                "synthesis": record.synthesis_json,
                "score": record.daily_candidate_score,
                "rank": record.daily_candidate_rank,
            }
            for record in stored
        ])
        self.assertNotIn("SECRET_PROVIDER_PAYLOAD", serialized)

    def test_one_model_call_maximum_is_enforced(self):
        ids = [record.public_id for record in self._recent_three()]
        response, provider = self.select_with(scoring_output(ids))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(provider.responses.create.call_count, 1)

    def test_model_novelty_field_is_rejected_and_cannot_affect_score(self):
        ids = [record.public_id for record in self._recent_two()]
        output = scoring_output(ids)
        output["candidates"][0]["novelty_score"] = 0
        response, _ = self.select_with(output)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"]["code"], "invalid_model_output")

    def test_scoring_contract_excludes_model_novelty(self):
        ids = [record.public_id for record in self._recent_two()]
        response, provider = self.select_with(scoring_output(ids))
        self.assertEqual(response.status_code, 200)
        schema = provider.responses.create.call_args.kwargs["text"]["format"]["schema"]
        properties = schema["properties"]["candidates"]["items"]["properties"]
        self.assertNotIn("novelty_score", properties)
        self.assertNotIn("novelty_score", provider.responses.create.call_args.kwargs["input"])

    def test_arbitrary_browser_candidate_json_is_rejected_without_model_call(self):
        self.archive_many(2)
        with patch.object(cyberslooth, "create_openai_client") as create_client:
            response = self.client.post("/api/select-daily-candidate", json={"records": []})
        self.assertEqual(response.status_code, 400)
        create_client.assert_not_called()

    def test_missing_api_key_fails_without_changing_records(self):
        self.archive_many(2)
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post("/api/select-daily-candidate")
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(archive_store.get_current_daily_candidate())

    def test_malformed_provider_response_fails_safely(self):
        self.archive_many(2)
        provider = self.provider_for({"not": "the schema"})
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ):
            response = self.client.post("/api/select-daily-candidate")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"]["code"], "invalid_model_output")

    def test_provider_failure_fails_safely_without_retry(self):
        self.archive_many(2)
        provider = MagicMock()
        provider.responses.create.side_effect = openai.APIConnectionError(
            request=httpx.Request("POST", "https://api.openai.com/v1/responses")
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ):
            response = self.client.post("/api/select-daily-candidate")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"]["code"], "provider_error")
        self.assertEqual(provider.responses.create.call_count, 1)

    def test_database_write_failure_preserves_existing_selection(self):
        ids = [record.public_id for record in self._recent_two()]
        selected_id = self.select_with(scoring_output(ids))[0].get_json()["selected_public_id"]
        provider = self.provider_for(scoring_output(ids))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch.object(
            cyberslooth, "create_openai_client", return_value=provider
        ), patch.object(cyberslooth, "persist_daily_candidate_evaluation", side_effect=SQLAlchemyError("write failed")):
            response = self.client.post("/api/select-daily-candidate")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(archive_store.get_current_daily_candidate().public_id, selected_id)

    def test_stage_06_columns_are_added_to_existing_table(self):
        legacy_path = Path(self.temp.name) / "legacy-stage-05.db"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute("CREATE TABLE research_runs (id INTEGER PRIMARY KEY)")
            connection.commit()
        finally:
            connection.close()
        archive_store.configure_database("sqlite:///" + legacy_path.as_posix())
        columns = {column["name"] for column in inspect(archive_store._engine).get_columns("research_runs")}
        self.assertTrue({
            "daily_candidate_score", "daily_candidate_rank", "daily_candidate_selected",
            "daily_candidate_evaluated_at",
        }.issubset(columns))
        archive_store._engine.dispose()

    def test_canonical_url_equivalence_is_conservative(self):
        first = cyberslooth.canonicalize_publication_url("HTTPS://Example.COM:443#section")
        second = cyberslooth.canonicalize_publication_url("https://example.com/")
        self.assertEqual(first, second)
        self.assertNotEqual(
            cyberslooth.canonicalize_publication_url("https://example.com/resource"),
            cyberslooth.canonicalize_publication_url("https://example.com/resource/"),
        )

    def test_recent_exact_url_is_ineligible_but_archive_is_preserved(self):
        now = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
        record = self.archive_url("https://example.com/source", "Repeated source")
        eligible, scores, excluded = cyberslooth.apply_publication_novelty(
            [record],
            [{"published_at": now - timedelta(days=1), "final_url": "HTTPS://EXAMPLE.COM:443/source#top"}],
            now,
        )
        self.assertEqual(eligible, [])
        self.assertEqual(scores, {})
        self.assertEqual(excluded, [record.public_id])
        self.assertIsNotNone(archive_store.get_research_run(record.public_id))

    def test_domain_novelty_scores_are_deterministic(self):
        now = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
        records = [
            SimpleNamespace(public_id="CS-NEW", final_url="https://new.example/item"),
            SimpleNamespace(public_id="CS-MID", final_url="https://mid.example/new-item"),
            SimpleNamespace(public_id="CS-RECENT", final_url="https://recent.example/new-item"),
        ]
        history = [
            {"published_at": now - timedelta(days=10), "final_url": "https://mid.example/old-item"},
            {"published_at": now - timedelta(days=3), "final_url": "https://recent.example/old-item"},
        ]
        first = cyberslooth.apply_publication_novelty(records, history, now)
        second = cyberslooth.apply_publication_novelty(records, history, now)
        self.assertEqual(first, second)
        self.assertEqual(first[1], {"CS-NEW": 5, "CS-MID": 2, "CS-RECENT": 0})

    def test_different_url_on_recent_domain_remains_eligible(self):
        now = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
        record = SimpleNamespace(public_id="CS-DIFFERENT", final_url="https://example.com/new")
        eligible, scores, excluded = cyberslooth.apply_publication_novelty(
            [record],
            [{"published_at": now - timedelta(days=2), "final_url": "https://example.com/old"}],
            now,
        )
        self.assertEqual(eligible, [record])
        self.assertEqual(scores[record.public_id], 0)
        self.assertEqual(excluded, [])

    def test_exact_source_outside_cooldown_is_eligible_again(self):
        now = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
        record = SimpleNamespace(public_id="CS-RETURN", final_url="https://example.com/source")
        eligible, scores, _ = cyberslooth.apply_publication_novelty(
            [record],
            [{"published_at": now - timedelta(days=31), "final_url": "https://example.com/source"}],
            now,
        )
        self.assertEqual(eligible, [record])
        self.assertEqual(scores[record.public_id], 5)

    def test_previously_published_record_cannot_win_again(self):
        published = self.archive_url("https://published.example/source", "Published")
        fresh = self.archive_url("https://fresh.example/source", "Fresh")
        archive_store.publish_daily_discovery(
            research_public_id=published.public_id,
            source_autonomous_run_id="AR-20260908-ABCDEF",
            selection_reason="Previously selected.",
            selected_score=20,
            published_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        response, provider = self.select_with(scoring_output([fresh.public_id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["selected_public_id"], fresh.public_id)
        model_input = json.loads(provider.responses.create.call_args.kwargs["input"])
        self.assertEqual([item["public_id"] for item in model_input["records"]], [fresh.public_id])

    def test_deterministic_novelty_changes_final_ranking(self):
        published = self.archive_url("https://recent.example/old", "Published")
        same_domain = self.archive_url("https://recent.example/new", "Same domain")
        novel_domain = self.archive_url("https://novel.example/new", "Novel domain")
        archive_store.publish_daily_discovery(
            research_public_id=published.public_id,
            source_autonomous_run_id="AR-20260908-ABCDEF",
            selection_reason="Previously selected.",
            selected_score=20,
            published_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        output = scoring_output(
            [same_domain.public_id, novel_domain.public_id],
            selected=same_domain.public_id,
        )
        response, _ = self.select_with(output)
        ranked = response.get_json()["ranked"]
        self.assertEqual(response.get_json()["selected_public_id"], novel_domain.public_id)
        self.assertEqual(
            {item["public_id"]: item["novelty_score"] for item in ranked},
            {same_domain.public_id: 0, novel_domain.public_id: 5},
        )

    def test_publication_history_query_is_bounded_by_time(self):
        recent = self.archive_url("https://recent.example/source", "Recent")
        old = self.archive_url("https://old.example/source", "Old")
        now = datetime.now(timezone.utc)
        archive_store.publish_daily_discovery(
            research_public_id=recent.public_id,
            source_autonomous_run_id="AR-20260908-ABCDEF",
            selection_reason="Recent.", selected_score=20,
            published_at=now - timedelta(days=5),
        )
        archive_store.publish_daily_discovery(
            research_public_id=old.public_id,
            source_autonomous_run_id="AR-20260730-ABCDEF",
            selection_reason="Old.", selected_score=20,
            published_at=now - timedelta(days=40),
        )
        history = archive_store.list_recent_daily_discovery_history(now - timedelta(days=30))
        self.assertEqual([item["research_public_id"] for item in history], [recent.public_id])
        self.assertEqual(history[0]["final_url"], "https://recent.example/source")

    def test_no_eligible_discovery_skips_model_and_clears_candidate(self):
        first = self.archive_url("https://example.com/", "First repeat")
        self.archive_url("HTTPS://EXAMPLE.COM:443#again", "Second repeat")
        archive_store.publish_daily_discovery(
            research_public_id=first.public_id,
            source_autonomous_run_id="AR-20260908-ABCDEF",
            selection_reason="Previously selected.",
            selected_score=20,
            published_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        with patch.object(cyberslooth, "create_openai_client") as create_client:
            response = self.client.post("/api/select-daily-candidate")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["outcome"], "no_eligible_discovery")
        self.assertIsNone(response.get_json()["selected_public_id"])
        create_client.assert_not_called()
        self.assertIsNone(archive_store.get_current_daily_candidate())

    def _recent_two(self):
        self.archive_many(2)
        return archive_store.list_recent_research_runs()

    def _recent_three(self):
        self.archive_many(3)
        return archive_store.list_recent_research_runs()


if __name__ == "__main__":
    unittest.main()
