import json
import tempfile
import unittest
from pathlib import Path

import eval_rerun


class IncompleteManifestTests(unittest.TestCase):
    def test_incomplete_check_is_never_reused(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            task_files = root / "task_files"
            deliverables = root / "deliverables"
            task_files.mkdir()
            deliverables.mkdir()
            hashes = eval_rerun.compute_hashes(task_files, deliverables, scenario=1)
            manifest = {
                "version": eval_rerun.MANIFEST_VERSION,
                "scenario": 1,
                "hashes": hashes,
                "verdicts": {
                    "input_validation": "PASS",
                    "check1": "INCOMPLETE",
                    "check2": "PASS",
                },
            }
            path = eval_rerun.manifest_path(deliverables)
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")

            plan = eval_rerun.build_plan(task_files, deliverables, scenario=1, fresh=False)

            self.assertEqual(plan["stages"]["check1"]["action"], "RUN")
            self.assertEqual(plan["stages"]["check1"]["reason"], "prior_incomplete")
            self.assertEqual(plan["stages"]["check2"]["action"], "SKIP")

    def test_incomplete_is_a_valid_verdict(self):
        self.assertEqual(eval_rerun._norm_verdict("incomplete"), "INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
