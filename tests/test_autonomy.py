import copy
import io
import json
import logging
import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import SQLAlchemyError

import app as cyberslooth
import archive_store
import autonomy
import autonomous_run
from test_explore import CANDIDATE_A, CANDIDATE_B, evidence_record, fetched, follow_up_analysis, synthesis, valid_analysis


def starting_fetch():
    return {
        "requested_url": "https://example.com/",
        "final_url": "https://example.com/",
        "status_code": 200,
        "content_type": "text/html",
        "retrieved_at": "2026-09-03T21:00:00Z",
        "body": (
            f"<title>Starting page</title><main>A starting research lead.</main>"
            f"<a href='{CANDIDATE_A}'>A</a><a href='{CANDIDATE_B}'>B</a>"
        ),
    }


def exploration_result(original_evidence, original_analysis, *, fail_second=False):
    urls = [CANDIDATE_A, CANDIDATE_B]
    successful_urls = [CANDIDATE_A] if fail_second else urls
    comparison = synthesis(successful_urls)
    compared = {item["url"]: item for item in comparison["explored"]}
    explored = []
    for url in urls:
        if fail_second and url == CANDIDATE_B:
            explored.append({
                "url": url,
                "selection_reason": "Bounded selector choice.",
                "retrieval": {"status": "failed", "error": {"code": "source_timeout", "message": "Timed out."}},
                "analysis_status": {"status": "not_run", "error": None},
                "evidence": None,
                "analysis": None,
            })
            continue
        follow_evidence = cyberslooth.build_research_evidence(fetched(url))
        explored.append({
            "url": url,
            "selection_reason": "Bounded selector choice.",
            "retrieval": {"status": "success", "error": None},
            "analysis_status": {"status": "success", "error": None},
            "evidence": follow_evidence,
            "analysis": follow_up_analysis(),
            **compared[url],
        })
        explored[-1]["selection_reason"] = "Bounded selector choice."
    return {
        "original": {"evidence": original_evidence, "analysis": original_analysis},
        "selected_count": 2,
        "explored": explored,
        "model_calls": {"used": 3 if fail_second else 4, "maximum": 4},
        "stopped": {"value": True, "reason": "Follow-up budget reached."},
        "starting_point": comparison["starting_point"],
        "synthesis": comparison["synthesis"],
    }


class AutonomyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        database_path = (Path(self.temp.name) / "autonomy-test.db").as_posix()
        archive_store.configure_database("sqlite:///" + database_path)
        self.client = cyberslooth.app.test_client()
        self.seed = {
            "id": "seed-test", "url": "https://example.com/", "label": "Test seed",
            "category": "test directory", "enabled": True,
        }

    def tearDown(self):
        archive_store.configure_database("sqlite:///" + archive_store.LOCAL_DATABASE_PATH.as_posix())
        self.temp.cleanup()

    def archive_record(self, title="Prior archive"):
        evidence = copy.deepcopy(evidence_record(candidates=[]))
        analysis = copy.deepcopy(valid_analysis(candidates=[]))
        evidence["content"]["title"] = title
        analysis["summary"] = f"Stored summary for {title}."
        storage, fingerprint = cyberslooth.validate_archive_payload({"evidence": evidence, "analysis": analysis})
        return archive_store.create_research_run(storage, fingerprint)[0]

    @staticmethod
    def fake_scores(records):
        ranked = []
        for rank, record in enumerate(records, 1):
            ranked.append({
                "public_id": record.public_id,
                "research_value_score": 5,
                "evidence_quality_score": 5,
                "novelty_score": 4,
                "interestingness_score": 4,
                "uncertainty_penalty": rank - 1,
                "archive_quality_score": 4,
                "total_score": 22 - (rank - 1),
                "reason": f"Bounded rank {rank}.",
                "rank": rank,
            })
        return ranked, "Best validated evidence in the bounded recent set."

    def run_mocked(self, *, partial=False, explore=True, score_side_effect=None, publication_side_effect=None):
        self.archive_record()
        original_evidence = evidence_record()
        original_analysis = valid_analysis() if explore else valid_analysis(candidates=[])

        def fake_analysis(_evidence, budget):
            budget.consume()
            return copy.deepcopy(original_analysis)

        def fake_explore(evidence, analysis, budget):
            result = exploration_result(evidence, analysis, fail_second=partial)
            for _ in range(result["model_calls"]["used"]):
                budget.consume()
            return result, 200

        with ExitStack() as stack:
            stack.enter_context(patch.object(autonomy, "load_seed_pool", return_value=[self.seed]))
            stack.enter_context(patch.object(autonomy.cyberslooth, "fetch_public_page", return_value=starting_fetch()))
            stack.enter_context(patch.object(autonomy.cyberslooth, "analyze_evidence", side_effect=fake_analysis))
            if explore:
                stack.enter_context(patch.object(autonomy.cyberslooth, "explore_evidence", side_effect=fake_explore))
            score_patch = patch.object(autonomy.cyberslooth, "score_daily_candidates")
            mocked_score = stack.enter_context(score_patch)
            if score_side_effect:
                mocked_score.side_effect = score_side_effect
            else:
                mocked_score.side_effect = self.fake_scores
            if publication_side_effect:
                stack.enter_context(patch.object(autonomy, "publish_daily_discovery", side_effect=publication_side_effect))
            return autonomy.run_autonomous_expedition()

    def test_seed_pool_loads_enabled_seeds(self):
        seeds = autonomy.load_seed_pool()
        self.assertGreaterEqual(len([seed for seed in seeds if seed["enabled"]]), 5)

    def test_seed_selection_does_not_require_ai_call(self):
        with patch.object(cyberslooth, "create_openai_client") as create_client:
            selected = autonomy.select_seed([self.seed])
        self.assertEqual(selected["id"], "seed-test")
        create_client.assert_not_called()

    def test_seed_selection_avoids_most_recent_seed(self):
        recent = archive_store.create_autonomous_run()
        archive_store.set_autonomous_run_seed(recent.public_run_id, "seed-a", "https://example.com/a")
        archive_store.fail_autonomous_run(recent.public_run_id, failure_stage="test", failure_message_safe="test", pages_retrieved=0, model_calls_used=0)
        seeds = [
            {**self.seed, "id": "seed-a"},
            {**self.seed, "id": "seed-b"},
        ]
        self.assertEqual(autonomy.select_seed(seeds)["id"], "seed-b")

    def test_no_enabled_seeds_fails_safely(self):
        with self.assertRaises(autonomy.AutonomyError) as raised:
            autonomy.select_seed([{**self.seed, "enabled": False}])
        self.assertEqual(raised.exception.code, "no_enabled_seeds")

    def test_autonomous_run_creates_running_record(self):
        run = archive_store.create_autonomous_run()
        self.assertEqual(run.status, "running")
        self.assertRegex(run.public_run_id, r"^AR-\d{8}-[A-F0-9]{6}$")

    def test_successful_mocked_full_run_completes(self):
        result = self.run_mocked()
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(result["research_public_id"])
        self.assertIsNotNone(result["daily_discovery_public_id"])

    def test_starting_retrieval_failure_marks_run_failed(self):
        with patch.object(autonomy, "load_seed_pool", return_value=[self.seed]), patch.object(
            autonomy.cyberslooth, "fetch_public_page",
            side_effect=cyberslooth.IngestError("source_timeout", "The source timed out.", 504),
        ):
            with self.assertRaises(autonomy.AutonomyError):
                autonomy.run_autonomous_expedition()
        run = archive_store.get_latest_autonomous_run()
        self.assertEqual((run.status, run.failure_stage), ("failed", "retrieval"))
        self.assertEqual(archive_store.list_research_runs(), [])

    def test_starting_analysis_failure_marks_run_failed(self):
        with patch.object(autonomy, "load_seed_pool", return_value=[self.seed]), patch.object(
            autonomy.cyberslooth, "fetch_public_page", return_value=starting_fetch(),
        ), patch.object(
            autonomy.cyberslooth, "analyze_evidence",
            side_effect=cyberslooth.AnalysisError("provider_error", "Analysis failed.", 502),
        ):
            with self.assertRaises(autonomy.AutonomyError):
                autonomy.run_autonomous_expedition()
        run = archive_store.get_latest_autonomous_run()
        self.assertEqual((run.status, run.failure_stage), ("failed", "analysis"))

    def test_partial_stage_04_failure_can_complete(self):
        result = self.run_mocked(partial=True)
        self.assertEqual(result["status"], "completed")
        record = archive_store.get_research_run(result["research_public_id"])
        self.assertEqual(record.exploration_json["explored"][1]["retrieval"]["status"], "failed")

    def test_archive_failure_does_not_publish(self):
        self.archive_record()
        with patch.object(autonomy, "load_seed_pool", return_value=[self.seed]), patch.object(
            autonomy.cyberslooth, "fetch_public_page", return_value=fetched(self.seed["url"]),
        ), patch.object(
            autonomy.cyberslooth, "analyze_evidence", side_effect=lambda _e, b: (b.consume(), valid_analysis(candidates=[]))[1],
        ), patch.object(autonomy, "create_research_run", side_effect=SQLAlchemyError("archive failed")):
            with self.assertRaises(autonomy.AutonomyError):
                autonomy.run_autonomous_expedition()
        self.assertIsNone(archive_store.get_current_daily_discovery())

    def test_scoring_failure_preserves_archive_and_prior_discovery(self):
        prior = self.archive_record("Previously published")
        archive_store.publish_daily_discovery(
            research_public_id=prior.public_id, source_autonomous_run_id="AR-PRIOR",
            selection_reason="Prior reason.", selected_score=20,
            published_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        with self.assertRaises(autonomy.AutonomyError):
            self.run_mocked(explore=False, score_side_effect=cyberslooth.DailySelectionError("provider_error", "Scoring failed.", 502))
        self.assertGreaterEqual(len(archive_store.list_research_runs()), 2)
        self.assertEqual(archive_store.get_current_daily_discovery().research_run_public_id, prior.public_id)

    def test_publication_failure_preserves_previous_publication_and_scoring(self):
        prior = self.archive_record("Previously published")
        archive_store.publish_daily_discovery(
            research_public_id=prior.public_id, source_autonomous_run_id="AR-PRIOR",
            selection_reason="Prior reason.", selected_score=20,
            published_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        with self.assertRaises(autonomy.AutonomyError):
            self.run_mocked(explore=False, publication_side_effect=SQLAlchemyError("publish failed"))
        self.assertEqual(archive_store.get_current_daily_discovery().research_run_public_id, prior.public_id)
        self.assertIsNotNone(archive_store.get_current_daily_candidate())

    def test_at_most_two_follow_up_pages_are_recorded(self):
        result = self.run_mocked()
        record = archive_store.get_research_run(result["research_public_id"])
        self.assertLessEqual(record.exploration_json["selected_count"], 2)
        self.assertLessEqual(result["pages_retrieved"], 3)

    def test_at_most_six_total_model_calls_are_used(self):
        result = self.run_mocked()
        self.assertEqual(result["model_calls_used"], 6)

    def test_no_recursive_expedition_loop_occurs(self):
        self.archive_record()
        with patch.object(autonomy, "load_seed_pool", return_value=[self.seed]), patch.object(
            autonomy.cyberslooth, "fetch_public_page", return_value=fetched(self.seed["url"]),
        ) as fetch_mock, patch.object(
            autonomy.cyberslooth, "analyze_evidence", side_effect=lambda _e, b: (b.consume(), valid_analysis(candidates=[]))[1],
        ), patch.object(autonomy.cyberslooth, "score_daily_candidates", side_effect=self.fake_scores):
            result = autonomy.run_autonomous_expedition()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(fetch_mock.call_count, 1)

    def test_completed_run_cannot_run_twice_same_utc_day(self):
        self.run_mocked(explore=False)
        with self.assertRaises(autonomy.AutonomyError) as raised:
            autonomy.run_autonomous_expedition()
        self.assertEqual(raised.exception.code, "run_blocked")

    def test_concurrent_duplicate_run_is_blocked(self):
        archive_store.create_autonomous_run()
        with self.assertRaises(autonomy.AutonomyError) as raised:
            autonomy.run_autonomous_expedition()
        self.assertEqual(raised.exception.code, "run_blocked")

    def test_cli_uses_same_orchestrator(self):
        with patch.object(autonomous_run, "run_autonomous_expedition", return_value={"status": "completed"}) as orchestrator:
            self.assertEqual(autonomous_run.main(), 0)
        orchestrator.assert_called_once_with()

    def test_http_trigger_rejects_missing_or_incorrect_token(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.client.post("/api/autonomous-run").status_code, 503)
        with patch.dict(os.environ, {"AUTONOMY_RUN_TOKEN": "correct"}, clear=True):
            self.assertEqual(self.client.post("/api/autonomous-run", headers={"Authorization": "Bearer wrong"}).status_code, 401)

    def test_http_trigger_accepts_correct_token_with_mocked_run(self):
        result = {"public_run_id": "AR-20260903-ABCDEF", "status": "completed"}
        with patch.dict(os.environ, {"AUTONOMY_RUN_TOKEN": "correct"}, clear=True), patch.object(
            autonomy, "run_autonomous_expedition", return_value=result,
        ) as orchestrator:
            response = self.client.post("/api/autonomous-run", headers={"Authorization": "Bearer correct"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["run"], result)
        orchestrator.assert_called_once()

    def test_token_never_appears_in_response_or_logs(self):
        token = "TOKEN_MUST_STAY_SECRET"
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        cyberslooth.app.logger.addHandler(handler)
        try:
            with patch.dict(os.environ, {"AUTONOMY_RUN_TOKEN": token}, clear=True):
                response = self.client.post("/api/autonomous-run", headers={"Authorization": "Bearer wrong"})
        finally:
            cyberslooth.app.logger.removeHandler(handler)
        self.assertNotIn(token, response.get_data(as_text=True))
        self.assertNotIn(token, stream.getvalue())

    def test_today_empty_state_works(self):
        html = self.client.get("/today").get_data(as_text=True)
        self.assertIn("No autonomous discovery has been published yet", html)

    def test_today_published_state_and_archive_link_work(self):
        record = self.archive_record("Published discovery")
        archive_store.publish_daily_discovery(
            research_public_id=record.public_id, source_autonomous_run_id="AR-TEST",
            selection_reason="It ranked highest.", selected_score=21,
        )
        html = self.client.get("/today").get_data(as_text=True)
        self.assertIn("Published discovery", html)
        self.assertIn("It ranked highest.", html)
        self.assertIn(f"/archive/{record.public_id}", html)

    def test_status_returns_sanitized_run_information(self):
        run = archive_store.create_autonomous_run()
        archive_store.set_autonomous_run_seed(run.public_run_id, "secret-seed", "https://secret.example/path")
        archive_store.fail_autonomous_run(
            run.public_run_id, failure_stage="retrieval", failure_message_safe="Safe failure.",
            pages_retrieved=0, model_calls_used=0,
        )
        html = self.client.get("/status").get_data(as_text=True)
        self.assertIn(run.public_run_id, html)
        self.assertIn("FAILED", html)
        self.assertNotIn("secret-seed", html)
        self.assertNotIn("secret.example", html)

    def test_published_archive_detail_uses_daily_discovery_badge(self):
        record = self.archive_record("Published detail")
        archive_store.publish_daily_discovery(
            research_public_id=record.public_id, source_autonomous_run_id="AR-TEST",
            selection_reason="Selected.", selected_score=20,
        )
        html = self.client.get(f"/archive/{record.public_id}").get_data(as_text=True)
        self.assertIn("DAILY DISCOVERY", html)

    def test_existing_archive_persistence_remains_intact(self):
        record = self.archive_record("Still intact")
        self.assertEqual(archive_store.get_research_run(record.public_id).title, "Still intact")
        self.assertIsNone(archive_store.get_latest_autonomous_run())


if __name__ == "__main__":
    unittest.main()
