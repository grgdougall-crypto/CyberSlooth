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
            "category": "archives", "enabled": True,
        }
        self.seed_a = {**self.seed, "id": "seed-a", "url": "https://example.com/a"}
        self.seed_b = {**self.seed, "id": "seed-b", "url": "https://example.com/b"}

    def tearDown(self):
        archive_store.configure_database("sqlite:///" + archive_store.LOCAL_DATABASE_PATH.as_posix())
        self.temp.cleanup()

    def archive_record(self, title="Prior archive", url="https://example.com/"):
        evidence = copy.deepcopy(evidence_record(candidates=[]))
        analysis = copy.deepcopy(valid_analysis(candidates=[]))
        evidence["source"]["requested_url"] = url
        evidence["source"]["final_url"] = url
        evidence["content"]["title"] = title
        analysis["summary"] = f"Stored summary for {title}."
        storage, fingerprint = cyberslooth.validate_archive_payload({"evidence": evidence, "analysis": analysis})
        return archive_store.create_research_run(storage, fingerprint)[0]

    @staticmethod
    def fake_scores(records, novelty_scores):
        ranked = []
        for rank, record in enumerate(records, 1):
            novelty = novelty_scores[record.public_id]
            ranked.append({
                "public_id": record.public_id,
                "research_value_score": 5,
                "evidence_quality_score": 5,
                "novelty_score": novelty,
                "interestingness_score": 4,
                "uncertainty_penalty": rank - 1,
                "archive_quality_score": 4,
                "total_score": 18 + novelty - (rank - 1),
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

    def run_seed_scenario(self, fetch_outcomes, *, seeds=None):
        self.archive_record()
        selected_seeds = seeds or [self.seed_a, self.seed_b]

        def fake_analysis(_evidence, budget):
            budget.consume()
            return valid_analysis(candidates=[])

        with patch.object(autonomy, "load_seed_pool", return_value=selected_seeds), patch.object(
            autonomy.cyberslooth, "fetch_public_page", side_effect=fetch_outcomes,
        ) as fetch_mock, patch.object(
            autonomy.cyberslooth, "analyze_evidence", side_effect=fake_analysis,
        ) as analysis_mock, patch.object(
            autonomy.cyberslooth, "score_daily_candidates", side_effect=self.fake_scores,
        ) as score_mock:
            try:
                result = autonomy.run_autonomous_expedition()
                error = None
            except autonomy.AutonomyError as exc:
                result = None
                error = exc
        return result, error, fetch_mock, analysis_mock, score_mock

    def test_seed_pool_loads_enabled_seeds(self):
        seeds = autonomy.load_seed_pool()
        self.assertEqual(len(seeds), 20)
        self.assertEqual(len([seed for seed in seeds if seed["enabled"]]), 20)
        self.assertEqual({seed["category"] for seed in seeds}, autonomy.SEED_CATEGORIES)

    def write_seed_pool(self, seeds):
        path = Path(self.temp.name) / "seeds.json"
        path.write_text(json.dumps(seeds), encoding="utf-8")
        return path

    def test_unknown_seed_category_is_rejected(self):
        path = self.write_seed_pool([{**self.seed, "category": "miscellaneous"}])
        with self.assertRaises(autonomy.AutonomyError) as raised:
            autonomy.load_seed_pool(path)
        self.assertEqual(raised.exception.code, "seed_pool_invalid")

    def test_duplicate_enabled_canonical_seed_url_is_rejected(self):
        path = self.write_seed_pool([
            {**self.seed, "id": "seed-a", "url": "HTTPS://WWW.Example.COM:443/path#one"},
            {**self.seed, "id": "seed-b", "url": "https://example.com/path#two"},
        ])
        with self.assertRaises(autonomy.AutonomyError) as raised:
            autonomy.load_seed_pool(path)
        self.assertEqual(raised.exception.code, "seed_pool_invalid")

    def test_hostname_normalization_is_deterministic(self):
        self.assertEqual(
            autonomy.normalize_seed_hostname("HTTPS://WWW.Example.COM.:443/path"),
            "example.com",
        )
        self.assertEqual(
            autonomy.canonicalize_seed_url("HTTPS://WWW.Example.COM:443/path#fragment"),
            "https://example.com/path",
        )

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

    def test_never_used_category_is_selected_first(self):
        used_at = datetime(2026, 9, 10, tzinfo=timezone.utc)
        seeds = [
            {**self.seed, "id": "seed-a", "category": "archives"},
            {**self.seed, "id": "seed-b", "url": "https://history.example/", "category": "history-heritage"},
        ]
        with patch.object(autonomy, "autonomous_seed_last_used", return_value={"seed-a": used_at}):
            self.assertEqual(autonomy.select_seed(seeds)["id"], "seed-b")

    def test_oldest_category_is_selected_first(self):
        now = datetime(2026, 9, 10, tzinfo=timezone.utc)
        seeds = [
            {**self.seed, "id": "seed-a", "category": "archives"},
            {**self.seed, "id": "seed-b", "url": "https://history.example/", "category": "history-heritage"},
        ]
        history = {"seed-a": now, "seed-b": now - timedelta(days=4)}
        with patch.object(autonomy, "autonomous_seed_last_used", return_value=history):
            self.assertEqual(autonomy.select_seed(seeds)["id"], "seed-b")

    def test_oldest_hostname_is_selected_within_category(self):
        now = datetime(2026, 9, 10, tzinfo=timezone.utc)
        seeds = [
            {**self.seed, "id": "seed-a", "url": "https://recent.example/a"},
            {**self.seed, "id": "seed-b", "url": "https://older.example/b"},
        ]
        history = {"seed-a": now, "seed-b": now - timedelta(days=4)}
        with patch.object(autonomy, "autonomous_seed_last_used", return_value=history):
            self.assertEqual(autonomy.select_seed(seeds)["id"], "seed-b")

    def test_oldest_seed_is_selected_within_hostname(self):
        now = datetime(2026, 9, 10, tzinfo=timezone.utc)
        seeds = [
            {**self.seed, "id": "seed-a", "url": "https://example.com/a"},
            {**self.seed, "id": "seed-b", "url": "https://www.example.com/b"},
        ]
        history = {"seed-a": now, "seed-b": now - timedelta(days=4)}
        with patch.object(autonomy, "autonomous_seed_last_used", return_value=history):
            self.assertEqual(autonomy.select_seed(seeds)["id"], "seed-b")

    def test_seed_id_is_stable_final_tie_break(self):
        seeds = [
            {**self.seed, "id": "seed-b", "url": "https://example.com/b"},
            {**self.seed, "id": "seed-a", "url": "https://example.com/a"},
        ]
        with patch.object(autonomy, "autonomous_seed_last_used", return_value={}):
            self.assertEqual(autonomy.select_seed(seeds)["id"], "seed-a")

    def test_failed_attempt_affects_category_hostname_and_seed_recency(self):
        run = archive_store.create_autonomous_run()
        archive_store.set_autonomous_run_seed(run.public_run_id, "seed-a", "https://example.com/a")
        archive_store.fail_autonomous_run(
            run.public_run_id, failure_stage="retrieval", failure_message_safe="Unavailable.",
            pages_retrieved=0, model_calls_used=0,
        )
        seeds = [
            {**self.seed, "id": "seed-a", "url": "https://example.com/a", "category": "archives"},
            {**self.seed, "id": "seed-b", "url": "https://www.example.com/b", "category": "archives"},
            {**self.seed, "id": "seed-c", "url": "https://other.example/c", "category": "archives"},
            {**self.seed, "id": "seed-d", "url": "https://science.example/d", "category": "science-space"},
        ]
        self.assertEqual(autonomy.select_seed(seeds)["id"], "seed-d")
        self.assertEqual(autonomy.select_seed(seeds, excluded_ids={"seed-d"})["id"], "seed-c")
        self.assertEqual(autonomy.select_seed(seeds, excluded_ids={"seed-c", "seed-d"})["id"], "seed-b")

    def test_excluded_canonical_url_cannot_be_selected(self):
        seeds = [
            {**self.seed, "id": "seed-a", "url": "https://www.example.com/path#one"},
            {**self.seed, "id": "seed-b", "url": "https://example.com/path#two"},
            {**self.seed, "id": "seed-c", "url": "https://other.example/path"},
        ]
        with patch.object(autonomy, "autonomous_seed_last_used", return_value={}):
            selected = autonomy.select_seed(
                seeds, excluded_ids={"seed-a"}, excluded_urls={"HTTPS://EXAMPLE.COM:443/path"},
            )
        self.assertEqual(selected["id"], "seed-c")

    def test_disabled_seeds_are_ignored(self):
        seeds = [
            {**self.seed, "id": "seed-a", "enabled": False},
            {**self.seed, "id": "seed-b", "url": "https://other.example/"},
        ]
        with patch.object(autonomy, "autonomous_seed_last_used", return_value={}):
            self.assertEqual(autonomy.select_seed(seeds)["id"], "seed-b")

    def test_repeated_selection_reaches_every_seed_without_starvation(self):
        seeds = [
            {**self.seed, "id": "seed-a1", "url": "https://a1.example/", "category": "archives"},
            {**self.seed, "id": "seed-a2", "url": "https://a2.example/", "category": "archives"},
            {**self.seed, "id": "seed-h1", "url": "https://h1.example/", "category": "history-heritage"},
            {**self.seed, "id": "seed-h2", "url": "https://h2.example/", "category": "history-heritage"},
            {**self.seed, "id": "seed-s1", "url": "https://s1.example/", "category": "science-space"},
            {**self.seed, "id": "seed-s2", "url": "https://s2.example/", "category": "science-space"},
        ]
        history = {}
        chosen = []
        base = datetime(2026, 9, 1, tzinfo=timezone.utc)
        with patch.object(autonomy, "autonomous_seed_last_used", side_effect=lambda _ids: dict(history)):
            for index in range(12):
                selected = autonomy.select_seed(seeds)
                chosen.append(selected["id"])
                history[selected["id"]] = base + timedelta(days=index)
        self.assertEqual(set(chosen), {seed["id"] for seed in seeds})
        self.assertEqual({seed_id: chosen.count(seed_id) for seed_id in set(chosen)}, {
            seed["id"]: 2 for seed in seeds
        })

    def test_every_configured_enabled_seed_is_reachable(self):
        seeds = autonomy.load_seed_pool()
        history = {}
        chosen = []
        base = datetime(2026, 9, 1, tzinfo=timezone.utc)
        with patch.object(autonomy, "autonomous_seed_last_used", side_effect=lambda _ids: dict(history)):
            for index in range(len(seeds) * 2):
                selected = autonomy.select_seed(seeds)
                chosen.append(selected["id"])
                history[selected["id"]] = base + timedelta(days=index)
        self.assertEqual(set(chosen), {seed["id"] for seed in seeds if seed["enabled"]})

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
        self.assertEqual(result["outcome"], "discovery_published")
        self.assertIsNotNone(result["research_public_id"])
        self.assertIsNotNone(result["daily_discovery_public_id"])

    def test_stage_11_preserves_existing_expedition_budgets(self):
        self.assertEqual(autonomy.MAX_STARTING_SEED_ATTEMPTS, 2)
        self.assertEqual(autonomy.MAX_AUTONOMOUS_MODEL_CALLS, 6)
        self.assertEqual(cyberslooth.MAX_CANDIDATE_LINKS, 10)
        self.assertEqual(cyberslooth.MAX_FOLLOW_UPS, 2)
        self.assertEqual(cyberslooth.MAX_EXPLORE_MODEL_CALLS, 4)

    def test_no_eligible_discovery_completes_without_publication_or_scoring_call(self):
        prior = self.archive_record("Previously published")
        published_at = datetime.now(timezone.utc) - timedelta(days=1)
        archive_store.publish_daily_discovery(
            research_public_id=prior.public_id,
            source_autonomous_run_id="AR-20260908-ABCDEF",
            selection_reason="Previously selected.",
            selected_score=20,
            published_at=published_at,
        )
        original_analysis = valid_analysis(candidates=[])

        def fake_analysis(_evidence, budget):
            budget.consume()
            return copy.deepcopy(original_analysis)

        with patch.object(autonomy, "load_seed_pool", return_value=[self.seed]), patch.object(
            autonomy.cyberslooth, "fetch_public_page", return_value=starting_fetch(),
        ), patch.object(
            autonomy.cyberslooth, "analyze_evidence", side_effect=fake_analysis,
        ), patch.object(autonomy.cyberslooth, "score_daily_candidates") as score_mock:
            result = autonomy.run_autonomous_expedition()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["outcome"], "no_eligible_discovery")
        self.assertIsNone(result["daily_discovery_public_id"])
        self.assertEqual(result["model_calls_used"], 1)
        score_mock.assert_not_called()
        self.assertEqual(len(archive_store.list_research_runs()), 2)
        self.assertEqual(archive_store.get_current_daily_discovery().research_run_public_id, prior.public_id)
        run = archive_store.get_latest_autonomous_run()
        self.assertEqual(run.outcome_code, "no_eligible_discovery")
        self.assertIsNone(run.failure_stage)

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

    def test_primary_seed_success_uses_one_retrieval_attempt(self):
        result, error, fetch_mock, _analysis, _score = self.run_seed_scenario(
            [fetched(self.seed_a["url"])],
        )
        self.assertIsNone(error)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(fetch_mock.call_args_list, [unittest.mock.call(self.seed_a["url"])])
        run = archive_store.get_latest_autonomous_run()
        self.assertEqual((run.initial_seed_id, run.seed_id, run.seed_attempts), ("seed-a", "seed-a", 1))

    def test_http_403_tries_one_alternate_approved_seed(self):
        denied = cyberslooth.IngestError("source_status", "The source returned HTTP 403.", 422)
        result, error, fetch_mock, _analysis, _score = self.run_seed_scenario(
            [denied, fetched(self.seed_b["url"])],
        )
        self.assertIsNone(error)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            fetch_mock.call_args_list,
            [unittest.mock.call(self.seed_a["url"]), unittest.mock.call(self.seed_b["url"])],
        )
        run = archive_store.get_latest_autonomous_run()
        self.assertEqual((run.initial_seed_id, run.seed_id, run.seed_attempts), ("seed-a", "seed-b", 2))

    def test_http_404_tries_one_alternate_approved_seed(self):
        missing = cyberslooth.IngestError("source_status", "The source returned HTTP 404.", 422)
        result, error, fetch_mock, _analysis, _score = self.run_seed_scenario(
            [missing, fetched(self.seed_b["url"])],
        )
        self.assertIsNone(error)
        self.assertEqual(fetch_mock.call_count, 2)
        self.assertEqual(result["status"], "completed")

    def test_timeout_tries_one_alternate_approved_seed(self):
        timeout = cyberslooth.IngestError("source_timeout", "The source timed out.", 504)
        result, error, fetch_mock, _analysis, _score = self.run_seed_scenario(
            [timeout, fetched(self.seed_b["url"])],
        )
        self.assertIsNone(error)
        self.assertEqual(fetch_mock.call_count, 2)
        self.assertEqual(result["status"], "completed")

    def test_dns_failure_tries_one_alternate_approved_seed(self):
        dns_failure = cyberslooth.IngestError("dns_failed", "The source could not be resolved.", 422)
        result, error, fetch_mock, _analysis, _score = self.run_seed_scenario(
            [dns_failure, fetched(self.seed_b["url"])],
        )
        self.assertIsNone(error)
        self.assertEqual(fetch_mock.call_count, 2)
        self.assertEqual(result["status"], "completed")

    def test_alternate_success_continues_through_publication(self):
        denied = cyberslooth.IngestError("source_status", "The source returned HTTP 403.", 422)
        result, error, _fetch, analysis_mock, score_mock = self.run_seed_scenario(
            [denied, fetched(self.seed_b["url"])],
        )
        self.assertIsNone(error)
        self.assertEqual(analysis_mock.call_count, 1)
        self.assertEqual(score_mock.call_count, 1)
        self.assertIsNotNone(result["research_public_id"])
        self.assertIsNotNone(archive_store.get_current_daily_discovery())

    def test_both_seed_failures_stop_without_publication(self):
        errors = [
            cyberslooth.IngestError("source_status", "The source returned HTTP 403.", 422),
            cyberslooth.IngestError("source_timeout", "The source timed out.", 504),
        ]
        result, error, fetch_mock, analysis_mock, score_mock = self.run_seed_scenario(errors)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(fetch_mock.call_count, 2)
        analysis_mock.assert_not_called()
        score_mock.assert_not_called()
        run = archive_store.get_latest_autonomous_run()
        self.assertEqual((run.status, run.failure_stage, run.seed_attempts), ("failed", "retrieval", 2))
        self.assertIsNone(archive_store.get_current_daily_discovery())

    def test_alternate_never_retries_the_same_url(self):
        duplicate_url_seed = {**self.seed_b, "url": self.seed_a["url"]}
        seed_c = {**self.seed, "id": "seed-c", "url": "https://example.com/c"}
        denied = cyberslooth.IngestError("source_status", "The source returned HTTP 403.", 422)
        result, error, fetch_mock, _analysis, _score = self.run_seed_scenario(
            [denied, fetched(seed_c["url"])], seeds=[self.seed_a, duplicate_url_seed, seed_c],
        )
        self.assertIsNone(error)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            fetch_mock.call_args_list,
            [unittest.mock.call(self.seed_a["url"]), unittest.mock.call(seed_c["url"])],
        )

    def test_starting_seed_retrieval_never_exceeds_two_attempts(self):
        seed_c = {**self.seed, "id": "seed-c", "url": "https://example.com/c"}
        unavailable = cyberslooth.IngestError("source_unavailable", "The source was unavailable.", 502)
        result, error, fetch_mock, _analysis, _score = self.run_seed_scenario(
            [unavailable, unavailable, fetched(seed_c["url"])], seeds=[self.seed_a, self.seed_b, seed_c],
        )
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(fetch_mock.call_count, 2)

    def test_alternate_retrieval_introduces_no_model_calls(self):
        missing = cyberslooth.IngestError("source_status", "The source returned HTTP 404.", 422)
        result, error, _fetch, analysis_mock, score_mock = self.run_seed_scenario(
            [missing, fetched(self.seed_b["url"])],
        )
        self.assertIsNone(error)
        self.assertEqual(result["model_calls_used"], 2)
        self.assertEqual(analysis_mock.call_count, 1)
        self.assertEqual(score_mock.call_count, 1)

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
        prior = self.archive_record("Previously published", "https://prior.example/source")
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
        prior = self.archive_record("Previously published", "https://prior.example/source")
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
