import copy
import json
import os
import sqlite3
import tempfile
import unittest
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
    "novelty_score": 3,
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
        output = scoring_output(ids, {ids[0]: {"novelty_score": 6}})
        response, _ = self.select_with(output)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"]["code"], "score_out_of_range")

    def test_total_score_is_recomputed_server_side(self):
        ids = [record.public_id for record in self._recent_two()]
        response, _ = self.select_with(scoring_output(ids))
        self.assertEqual(response.get_json()["ranked"][0]["total_score"], 14)

    def test_tie_break_prefers_evidence_then_research_value(self):
        records = self._recent_two()
        newer, older = records[0].public_id, records[1].public_id
        overrides = {
            newer: {"research_value_score": 4, "evidence_quality_score": 3, "novelty_score": 0, "interestingness_score": 0, "archive_quality_score": 3, "uncertainty_penalty": 0},
            older: {"research_value_score": 1, "evidence_quality_score": 5, "novelty_score": 0, "interestingness_score": 0, "archive_quality_score": 4, "uncertainty_penalty": 0},
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

    def _recent_two(self):
        self.archive_many(2)
        return archive_store.list_recent_research_runs()

    def _recent_three(self):
        self.archive_many(3)
        return archive_store.list_recent_research_runs()


if __name__ == "__main__":
    unittest.main()
