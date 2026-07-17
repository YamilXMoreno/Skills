import tempfile
import unittest
from pathlib import Path

import validate_patch_artifacts as validator


VALID_DIFF = (
    "diff --git a/src/example.py b/src/example.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/src/example.py\n"
    "+++ b/src/example.py\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)


class PatchValidationTests(unittest.TestCase):
    def test_accepts_lf_unified_diff(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "valid.diff"
            path.write_text(VALID_DIFF, encoding="utf-8", newline="\n")
            validator.validate_patch(path)

    def test_rejects_crlf(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "crlf.diff"
            path.write_bytes(VALID_DIFF.replace("\n", "\r\n").encode())
            with self.assertRaisesRegex(ValueError, "CRLF"):
                validator.validate_patch(path)

    def test_rejects_duplicate_file_blocks(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "duplicate.diff"
            path.write_text(VALID_DIFF + VALID_DIFF, encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ValueError, "duplicate diff block"):
                validator.validate_patch(path)

    def test_rejects_prose_before_diff(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "prose.diff"
            path.write_text("Generated patch:\n" + VALID_DIFF, encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ValueError, "content before"):
                validator.validate_patch(path)


if __name__ == "__main__":
    unittest.main()
