import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import inspect, select

import app as cyberslooth
import archive_store
from test_explore import CANDIDATE_A, evidence_record, fetched, follow_up_analysis, synthesis, valid_analysis


def explored_run():
    original_evidence = evidence_record()
    original_analysis = valid_analysis()
    follow_evidence = cyberslooth.build_research_evidence(fetched(CANDIDATE_A))
    follow_analysis = follow_up_analysis()
    comparison = synthesis([CANDIDATE_A])
    explored_item = {
        "url": CANDIDATE_A,
        "selection_reason": "Highest-value original candidate.",
        "retrieval": {"status": "success", "error": None},
        "analysis_status": {"status": "success", "error": None},
        "evidence": follow_evidence,
        "analysis": follow_analysis,
        **comparison["explored"][0],
    }
    explored_item["selection_reason"] = "Highest-value original candidate."
    return {
        "evidence": original_evidence,
        "analysis": original_analysis,
        "exploration": {
            "original": {"evidence": original_evidence, "analysis": original_analysis},
            "selected_count": 1,
            "explored": [explored_item],
            "model_calls": {"used": 3, "maximum": 4},
            "stopped": {"value": True, "reason": "Follow-up budget reached."},
            "starting_point": comparison["starting_point"],
            "synthesis": comparison["synthesis"],
        },
    }


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        database_path = (Path(self.temp.name) / "archive-test.db").as_posix()
        self.database_url = "sqlite:///" + database_path
        archive_store.configure_database(self.database_url)
        self.client = cyberslooth.app.test_client()

    def tearDown(self):
        archive_store.configure_database("sqlite:///" + archive_store.LOCAL_DATABASE_PATH.as_posix())
        self.temp.cleanup()

    def archive(self, payload=None):
        payload = payload or {"evidence": evidence_record(), "analysis": valid_analysis()}
        return self.client.post("/api/archive", json=payload)

    def test_local_sqlite_initialization_works(self):
        engine = archive_store._engine
        self.assertEqual(engine.dialect.name, "sqlite")
        self.assertIn("research_runs", inspect(engine).get_table_names())

    def test_archive_rejects_malformed_payload(self):
        response = self.client.post("/api/archive", json={"evidence": {}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "invalid_archive")

    def test_valid_analyzed_only_run_can_be_archived(self):
        response = self.archive()
        self.assertEqual(response.status_code, 201)
        record = archive_store.get_research_run(response.get_json()["public_id"])
        self.assertFalse(record.exploration_performed)
        self.assertIsNone(record.exploration_json)
        self.assertIsNone(record.synthesis_json)

    def test_valid_explored_run_can_be_archived(self):
        response = self.archive(explored_run())
        self.assertEqual(response.status_code, 201)
        record = archive_store.get_research_run(response.get_json()["public_id"])
        self.assertTrue(record.exploration_performed)
        self.assertEqual(record.research_value, "medium")
        self.assertEqual(len(record.exploration_json["explored"]), 1)

    def test_explored_run_preserves_retrieved_page_when_analysis_failed(self):
        payload = explored_run()
        failed_evidence = cyberslooth.build_research_evidence(fetched("https://example.org/lead-b"))
        payload["exploration"]["selected_count"] = 2
        payload["exploration"]["model_calls"]["used"] = 4
        payload["exploration"]["explored"].append({
            "url": "https://example.org/lead-b",
            "selection_reason": "Second-ranked original candidate.",
            "retrieval": {"status": "success", "error": None},
            "analysis_status": {"status": "failed", "error": {"code": "provider_timeout", "message": "Analysis timed out."}},
            "evidence": failed_evidence,
            "analysis": None,
        })
        response = self.archive(payload)
        self.assertEqual(response.status_code, 201)
        record = archive_store.get_research_run(response.get_json()["public_id"])
        failed_item = record.exploration_json["explored"][1]
        self.assertEqual(failed_item["analysis_status"]["status"], "failed")
        self.assertIsNotNone(failed_item["evidence"])

    def test_public_id_is_not_raw_database_id(self):
        payload = self.archive().get_json()
        self.assertRegex(payload["public_id"], r"^CS-\d{8}-[A-F0-9]{6}$")
        self.assertEqual(payload["archive_url"], f"/archive/{payload['public_id']}")
        self.assertNotEqual(payload["public_id"], "1")

    def test_archive_index_lists_newest_records_first(self):
        first = {"evidence": evidence_record(), "analysis": valid_analysis()}
        second = {"evidence": evidence_record(), "analysis": valid_analysis()}
        second["evidence"]["content"]["title"] = "Newest Record"
        second["analysis"]["summary"] = "Newest summary."
        first["evidence"]["content"]["title"] = "Older Record"
        self.archive(first)
        self.archive(second)
        html = self.client.get("/archive").get_data(as_text=True)
        self.assertLess(html.index("Newest Record"), html.index("Older Record"))

    def test_archive_detail_displays_stored_record(self):
        public_id = self.archive(explored_run()).get_json()["public_id"]
        response = self.client.get(f"/archive/{public_id}")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(public_id, html)
        self.assertIn("Original Source", html)
        self.assertIn("Follow-up Expedition", html)
        self.assertIn("Suggested future lead", html)

    def test_unknown_public_id_returns_404(self):
        response = self.client.get("/archive/CS-20260903-FFFFFF")
        self.assertEqual(response.status_code, 404)

    def test_immediate_duplicate_is_idempotent(self):
        first = self.archive()
        second = self.archive()
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()["duplicate"])
        self.assertEqual(first.get_json()["public_id"], second.get_json()["public_id"])
        self.assertEqual(len(archive_store.list_research_runs()), 1)

    def test_stored_json_keeps_evidence_and_analysis_separate(self):
        source = evidence_record()
        analysis = valid_analysis()
        public_id = self.archive({"evidence": source, "analysis": analysis}).get_json()["public_id"]
        record = archive_store.get_research_run(public_id)
        self.assertEqual(record.original_evidence_json["source"], source["source"])
        self.assertEqual(record.original_analysis_json["summary"], analysis["summary"])
        self.assertNotIn("summary", record.original_evidence_json)
        self.assertNotIn("source", record.original_analysis_json)

    def test_explored_provenance_remains_separate(self):
        public_id = self.archive(explored_run()).get_json()["public_id"]
        record = archive_store.get_research_run(public_id)
        original_url = record.original_evidence_json["source"]["final_url"]
        follow_url = record.exploration_json["explored"][0]["evidence"]["source"]["final_url"]
        self.assertNotEqual(original_url, follow_url)
        self.assertEqual(follow_url, CANDIDATE_A)

    def test_database_url_falls_back_only_for_local_use(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(archive_store.configured_database_url().startswith("sqlite:///"))
        with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT": "production"}, clear=True):
            with self.assertRaises(RuntimeError):
                archive_store.configured_database_url()
        with patch.dict(os.environ, {"DATABASE_URL": "postgres://user:pass@example/db"}, clear=True):
            self.assertEqual(
                archive_store.configured_database_url(),
                "postgresql+psycopg://user:pass@example/db",
            )

    def test_untrusted_secret_metadata_is_not_persisted_or_echoed(self):
        secret = "SECRET_DO_NOT_STORE"
        payload = {"evidence": evidence_record(), "analysis": valid_analysis(), "api_key": secret}
        response = self.archive(payload)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(secret, response.get_data(as_text=True))
        self.assertEqual(archive_store.list_research_runs(), [])

    def test_environment_secret_is_never_added_to_stored_record(self):
        secret = "ENV_SECRET_DO_NOT_STORE"
        with patch.dict(os.environ, {"OPENAI_API_KEY": secret}, clear=False):
            public_id = self.archive().get_json()["public_id"]
        record = archive_store.get_research_run(public_id)
        public_record = cyberslooth.archive_record_view(record)
        self.assertNotIn(secret, json.dumps(public_record, default=str))

    def test_archive_payload_size_limit_is_enforced(self):
        oversized = json.dumps({"evidence": "x" * (cyberslooth.MAX_ARCHIVE_REQUEST_BYTES + 1), "analysis": {}})
        response = self.client.post("/api/archive", data=oversized, content_type="application/json")
        self.assertEqual(response.status_code, 413)
        self.assertIn(response.get_json()["error"]["code"], {"archive_too_large", "request_too_large"})


if __name__ == "__main__":
    unittest.main()
