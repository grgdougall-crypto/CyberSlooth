import copy
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

import app as cyberslooth
import archive_store
import autonomous_run
import autonomy
from test_explore import evidence_record, valid_analysis


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Stage10BScheduledExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        database_path = (Path(self.temp.name) / "stage-10b-test.db").as_posix()
        archive_store.configure_database("sqlite:///" + database_path)
        self.client = cyberslooth.app.test_client()

    def tearDown(self):
        archive_store.configure_database("sqlite:///" + archive_store.LOCAL_DATABASE_PATH.as_posix())
        self.temp.cleanup()

    def archive_record(self):
        evidence = copy.deepcopy(evidence_record(candidates=[]))
        analysis = copy.deepcopy(valid_analysis(candidates=[]))
        storage, fingerprint = cyberslooth.validate_archive_payload({"evidence": evidence, "analysis": analysis})
        return archive_store.create_research_run(storage, fingerprint)[0]

    @staticmethod
    def completed_result():
        return {
            "public_run_id": "AR-20260904-ABCDEF",
            "status": "completed",
            "research_public_id": "CS-20260904-123ABC",
            "daily_discovery_public_id": "CS-20260904-456DEF",
            "pages_retrieved": 1,
            "model_calls_used": 2,
        }

    def test_cli_invokes_shared_orchestrator_once_and_exits_successfully(self):
        output = io.StringIO()
        with patch.object(
            autonomous_run, "run_autonomous_expedition", return_value=self.completed_result(),
        ) as orchestrator, redirect_stdout(output):
            exit_code = autonomous_run.main()
        self.assertEqual(exit_code, 0)
        orchestrator.assert_called_once_with()
        self.assertIn("CyberSlooth autonomous run completed", output.getvalue())
        self.assertIn("run_id=AR-20260904-ABCDEF", output.getvalue())

    def test_cli_exits_nonzero_for_mocked_failed_run(self):
        failed = {
            "public_run_id": "AR-20260904-ABCDEF",
            "status": "failed",
            "failure_stage": "retrieval",
        }
        output = io.StringIO()
        with patch.object(autonomous_run, "run_autonomous_expedition", return_value=failed), redirect_stdout(output):
            exit_code = autonomous_run.main()
        self.assertEqual(exit_code, 1)
        self.assertIn("CyberSlooth autonomous run failed", output.getvalue())
        self.assertIn("failure_stage=retrieval", output.getvalue())

    def test_cli_does_not_require_http_trigger_token(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch.object(
            autonomous_run, "run_autonomous_expedition", return_value=self.completed_result(),
        ) as orchestrator, redirect_stdout(output):
            exit_code = autonomous_run.main()
        self.assertEqual(exit_code, 0)
        orchestrator.assert_called_once_with()

    def test_http_endpoint_still_requires_token(self):
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post("/api/autonomous-run")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error"]["code"], "trigger_unavailable")

    def test_duplicate_scheduled_invocation_creates_no_run_or_discovery(self):
        record = self.archive_record()
        run = archive_store.create_autonomous_run()
        published = archive_store.publish_daily_discovery(
            research_public_id=record.public_id,
            source_autonomous_run_id=run.public_run_id,
            selection_reason="Existing daily publication.",
            selected_score=20,
            published_at=datetime.now(timezone.utc),
        )
        archive_store.complete_autonomous_run(
            run.public_run_id,
            research_public_id=record.public_id,
            daily_discovery_public_id=record.public_id,
            pages_retrieved=1,
            model_calls_used=2,
        )

        output = io.StringIO()
        with patch.object(
            autonomous_run, "run_autonomous_expedition", side_effect=autonomy.run_autonomous_expedition,
        ), redirect_stdout(output):
            exit_code = autonomous_run.main()

        with archive_store.database_session() as session:
            run_count = session.scalar(select(func.count()).select_from(archive_store.AutonomousRun))
            discovery_count = session.scalar(select(func.count()).select_from(archive_store.DailyDiscovery))
        self.assertEqual(exit_code, 1)
        self.assertIn("failure_stage=idempotency", output.getvalue())
        self.assertEqual(run_count, 1)
        self.assertEqual(discovery_count, 1)
        self.assertEqual(archive_store.get_current_daily_discovery().id, published.id)

    def test_railway_without_database_url_fails_closed(self):
        environment = os.environ.copy()
        environment.pop("DATABASE_URL", None)
        environment["RAILWAY_ENVIRONMENT"] = "production"
        environment["AUTONOMY_SCHEDULE_ENABLED"] = "true"
        process = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "autonomous_run.py")],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        combined = process.stdout + process.stderr
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("failure_stage=configuration", combined)
        self.assertNotIn("sqlite", combined.lower())

    def test_status_defaults_to_manual_mode(self):
        with patch.dict(os.environ, {}, clear=True):
            html = self.client.get("/status").get_data(as_text=True)
        self.assertIn("MANUAL TRIGGER", html)
        self.assertNotIn("<dt>Cadence</dt>", html)

    def test_status_shows_informational_scheduled_mode(self):
        environment = {
            "AUTONOMY_SCHEDULE_ENABLED": "true",
            "AUTONOMY_SCHEDULE_CRON": "0 7 * * *",
        }
        with patch.dict(os.environ, environment, clear=True):
            html = self.client.get("/status").get_data(as_text=True)
        self.assertIn("SCHEDULED", html)
        self.assertIn("DAILY", html)
        self.assertIn("0 7 * * *", html)
        self.assertIn("Railway triggers the short-lived process", html)

    def test_schedule_value_is_sanitized_and_not_executed(self):
        environment = {
            "AUTONOMY_SCHEDULE_ENABLED": "true",
            "AUTONOMY_SCHEDULE_CRON": "0 7 * * *<script>alert(1)</script>",
        }
        with patch.dict(os.environ, environment, clear=True):
            html = self.client.get("/status").get_data(as_text=True)
        self.assertNotIn("<script>", html)
        self.assertNotIn("alert(1)", html)
        self.assertIn("0 7 * * *?script?alert?1??/script?", html)

    def test_today_describes_two_approved_seed_attempts(self):
        record = self.archive_record()
        archive_store.publish_daily_discovery(
            research_public_id=record.public_id,
            source_autonomous_run_id="AR-20260904-ABCDEF",
            selection_reason="Selected.",
            selected_score=20,
        )
        html = self.client.get("/today").get_data(as_text=True)
        self.assertIn("One successful starting seed; maximum two approved seed attempts", html)
        self.assertIn("No arbitrary fallback browsing", html)

    def test_cli_output_uses_only_safe_whitelisted_fields(self):
        result = {
            **self.completed_result(),
            "OPENAI_API_KEY": "SECRET_OPENAI_VALUE",
            "DATABASE_URL": "postgresql://SECRET_DATABASE_VALUE",
            "AUTONOMY_RUN_TOKEN": "SECRET_TRIGGER_VALUE",
            "source_content": "SECRET_SOURCE_CONTENT",
        }
        output = io.StringIO()
        with patch.object(autonomous_run, "run_autonomous_expedition", return_value=result), redirect_stdout(output):
            self.assertEqual(autonomous_run.main(), 0)
        rendered = output.getvalue()
        for secret in ("SECRET_OPENAI_VALUE", "SECRET_DATABASE_VALUE", "SECRET_TRIGGER_VALUE", "SECRET_SOURCE_CONTENT"):
            self.assertNotIn(secret, rendered)

    def test_exception_message_and_secrets_are_not_printed_or_public(self):
        class SafeFailure(Exception):
            code = "provider_error"
            public_run_id = "AR-20260904-ABCDEF"
            failure_stage = "analysis"

        output = io.StringIO()
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "SECRET_OPENAI_VALUE",
            "DATABASE_URL": "postgresql://SECRET_DATABASE_VALUE",
            "AUTONOMY_RUN_TOKEN": "SECRET_TRIGGER_VALUE",
        }, clear=True), patch.object(
            autonomous_run, "run_autonomous_expedition", side_effect=SafeFailure("SECRET_PROVIDER_RESPONSE"),
        ), redirect_stdout(output):
            self.assertEqual(autonomous_run.main(), 1)
            status_html = self.client.get("/status").get_data(as_text=True)
            today_html = self.client.get("/today").get_data(as_text=True)
        combined = output.getvalue() + status_html + today_html
        for secret in (
            "SECRET_OPENAI_VALUE", "SECRET_DATABASE_VALUE", "SECRET_TRIGGER_VALUE", "SECRET_PROVIDER_RESPONSE",
        ):
            self.assertNotIn(secret, combined)

    def test_no_internal_scheduler_loop_or_dependency_is_added(self):
        entrypoint = (PROJECT_ROOT / "autonomous_run.py").read_text(encoding="utf-8")
        requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertNotIn("while ", entrypoint)
        for dependency in ("apscheduler", "celery", "redis"):
            self.assertNotIn(dependency, requirements)


if __name__ == "__main__":
    unittest.main()
