import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_harbor_models as runner


class DeadClassificationTests(unittest.TestCase):
    def test_classifies_auth_block(self):
        self.assertEqual(
            runner.classify_dead("provider returned 403: API key blocked")[0],
            "AUTH_BLOCKED",
        )

    def test_classifies_missing_provider_key(self):
        self.assertEqual(
            runner.classify_dead("GEMINI_API_KEY unset")[0],
            "AUTH_BLOCKED",
        )

    def test_classifies_model_down(self):
        self.assertEqual(
            runner.classify_dead("503 service unavailable for model"),
            ("PROVIDER_DOWN", "503"),
        )

    def test_does_not_mark_task_failure_dead(self):
        self.assertIsNone(runner.classify_dead("tests failed after 200 steps"))


class BatchTests(unittest.TestCase):
    def test_dead_model_does_not_cancel_siblings(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            configs = []
            for model in ("model-a", "model-b", "model-c"):
                config = root / f"{model}.json"
                config.write_text("{}", encoding="utf-8")
                configs.append((model, config))

            outcomes = {
                "model-a": runner.ModelResult("COMPLETED", "", "a.log", exit_code=0),
                "model-b": runner.ModelResult(
                    "DEAD",
                    "",
                    "b.log",
                    exit_code=1,
                    reason_code="AUTH_BLOCKED",
                ),
                "model-c": runner.ModelResult("COMPLETED", "", "c.log", exit_code=0),
            }

            def fake_run(model, config, harbor_bin, logs_dir):
                return outcomes[model]

            with patch.object(runner, "run_model", side_effect=fake_run) as mocked:
                state = runner.run_batch(
                    check="check1",
                    model_configs=configs,
                    state_file=root / "state.json",
                    logs_dir=root / "logs",
                    harbor_bin="harbor",
                    retry_dead=False,
                )

            self.assertEqual(mocked.call_count, 3)
            self.assertEqual(state["models"]["model-a"]["status"], "COMPLETED")
            self.assertEqual(state["models"]["model-b"]["status"], "DEAD")
            self.assertEqual(state["models"]["model-c"]["status"], "COMPLETED")

    def test_persisted_dead_model_is_skipped(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "model.json"
            config.write_text("{}", encoding="utf-8")
            state_file = root / "state.json"
            state_file.write_text(
                '{"models":{"model-b":{"status":"DEAD","reason_code":"AUTH_BLOCKED"}}}',
                encoding="utf-8",
            )

            with patch.object(runner, "run_model") as mocked:
                state = runner.run_batch(
                    check="check2",
                    model_configs=[("model-b", config)],
                    state_file=state_file,
                    logs_dir=root / "logs",
                    harbor_bin="harbor",
                    retry_dead=False,
                )

            mocked.assert_not_called()
            self.assertTrue(state["models"]["model-b"]["skipped"])

    def test_preflight_dead_model_is_never_launched(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "model.json"
            config.write_text("{}", encoding="utf-8")

            with patch.object(runner, "run_model") as mocked:
                state = runner.run_batch(
                    check="check1",
                    model_configs=[("model-c", config)],
                    state_file=root / "state.json",
                    logs_dir=root / "logs",
                    harbor_bin="harbor",
                    retry_dead=False,
                    marked_dead={"model-c": "provider status page reports outage"},
                )

            mocked.assert_not_called()
            self.assertEqual(state["models"]["model-c"]["status"], "DEAD")
            self.assertEqual(
                state["models"]["model-c"]["reason_code"],
                "PREFLIGHT_DEAD",
            )


if __name__ == "__main__":
    unittest.main()
