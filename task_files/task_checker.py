#!/usr/bin/env python3
"""
Task Checker - Patch Evaluation Script

Evaluates if the relevant tests have a F2P behavior before/after the golden_patch_obstructed is applied by:
1. Reverting any previously applied patches (safe re-run)
2. Applying test_patch_obstructed (adds tests with blockers)
3. Running tests → should FAIL
4. Applying golden_patch_obstructed (adds solution for the blockers)
5. Running tests → should PASS
6. Comparing: FAIL→PASS = SUCCESS

Usage:
------
# Using tests from file (default: task_files/relevant_tests.txt)
python task_files/task_checker.py

# Download task files from remote using instance_id
python task_files/task_checker.py --instance-id instance_qutebrowser__qutebrowser-52708364b5f91e198defb022d1a5b4b3ebd9b563-v2ef375ac784985212b1805e1d0431dc8f1b3c171

# Using tests as JSON array argument (IMPORTANT!: wrap in quotes)
python task_files/task_checker.py '["tests/unit/foo.py::test_one", "tests/unit/foo.py::test_two"]'

# With custom patch files
python task_files/task_checker.py --test-patch /app/my_test.diff --golden-patch /app/my_golden.diff

# Keep changes after evaluation (don't cleanup)
python task_files/task_checker.py --no-cleanup

Arguments:
----------
positional:
  tests_json          JSON array of test names (optional, overrides --tests-file)

options:
  --instance-id       Instance ID to download parsing_script.py and run_script.sh from remote.
                      Downloads from: https://static.remotasks.com/uploads/671fd8b00c6fe2f958ad3484/<instance_id>_<file>
  --tests-file        Path to file containing JSON array of tests (default: task_files/relevant_tests.txt)
  --test-patch        Path to patch that adds tests (default: /app/test_patch_obstructed.diff)
  --golden-patch      Path to patch with implementation (default: /app/golden_patch_obstructed.diff)
  --timeout           Timeout for test execution in seconds (default: 300)
  --no-cleanup        Don't reset git state after evaluation

Exit Codes:
-----------
  0  SUCCESS - All tests passed F2P check
  1  FAILURE - Tests did not follow FAIL→PASS pattern (generic/mixed)
  2  ERROR   - Failed to download task files
  3  ERROR   - Required file not found
  4  ERROR   - Failed to apply patch (generic, deprecated)
  5  ERROR   - Tests timed out
  6  ERROR   - Failed to parse test results
  8  ERROR   - Failed to apply test_patch.diff
  9  ERROR   - Failed to apply golden_patch.diff
 10  FAILURE - Expected tests not found in after results
 11  FAILURE - Tests passed before golden patch (should have failed)
 12  FAILURE - Tests failed after golden patch (should have passed)

Example with task_files/relevant_tests.txt:
--------
python task_files/task_checker.py

Example with the JSON array argument:
--------
python task_files/task_checker.py '[
  "tests/unit/config/test_configtypes.py::TestStatusbarWidget::test_blocker1_correct_prefix",
  "tests/unit/config/test_configtypes.py::TestStatusbarWidget::test_blocker1_old_prefix_rejected"
]'
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

# Base URL for downloading task files
REMOTE_BASE_URL = "https://static.remotasks.com/uploads/671fd8b00c6fe2f958ad3484"

# Default timeout for test execution (seconds)
DEFAULT_TEST_TIMEOUT = 300  # 5 minutes

# Exit codes
EXIT_SUCCESS = 0
EXIT_FAILURE = 1  # Generic F2P failure
EXIT_ERROR_DOWNLOAD = 2
EXIT_ERROR_FILE_NOT_FOUND = 3
EXIT_ERROR_PATCH_FAILED = 4  # Generic patch failure (deprecated, use specific ones)
EXIT_ERROR_TESTS_TIMEOUT = 5
EXIT_ERROR_PARSE_FAILED = 6
EXIT_ERROR_SETUP_PATCH_FAILED = 7  # Not used in task_checker.py, but kept for consistency
EXIT_ERROR_TEST_PATCH_FAILED = 8
EXIT_ERROR_GOLDEN_PATCH_FAILED = 9
EXIT_TESTS_NOT_FOUND = 10  # Expected tests not found in results
EXIT_TESTS_PASSED_BEFORE = 11  # Tests passed before golden patch (should have failed)
EXIT_TESTS_FAILED_AFTER = 12  # Tests failed after golden patch (should have passed)

# Error code descriptions (for mapping in validation scripts)
ERROR_DESCRIPTIONS = {
    EXIT_SUCCESS: "All tests passed F2P check",
    EXIT_FAILURE: "F2P validation failed - multiple issue types",
    EXIT_ERROR_DOWNLOAD: "Failed to download task files",
    EXIT_ERROR_FILE_NOT_FOUND: "Required file not found",
    EXIT_ERROR_PATCH_FAILED: "Failed to apply patch (generic)",
    EXIT_ERROR_TESTS_TIMEOUT: "Tests timed out",
    EXIT_ERROR_PARSE_FAILED: "Failed to parse test results",
    EXIT_ERROR_SETUP_PATCH_FAILED: "Failed to apply setup_patch.diff",
    EXIT_ERROR_TEST_PATCH_FAILED: "Failed to apply test_patch.diff",
    EXIT_ERROR_GOLDEN_PATCH_FAILED: "Failed to apply golden_patch.diff",
    EXIT_TESTS_NOT_FOUND: "Expected tests not found in after results",
    EXIT_TESTS_PASSED_BEFORE: "Tests passed before golden patch (should have failed)",
    EXIT_TESTS_FAILED_AFTER: "Tests failed after golden patch (should have passed)",
}


class TaskCheckerError(Exception):
    """Custom exception for task checker errors."""
    def __init__(self, message, exit_code=EXIT_FAILURE):
        self.message = message
        self.exit_code = exit_code
        super().__init__(self.message)

def download_task_files(instance_id):
    """Download parsing_script.py and run_script.sh from remote based on instance_id."""
    print("\n" + "="*60)
    print("DOWNLOADING TASK FILES")
    print("="*60)
    print(f"Instance ID: {instance_id}")
    
    files_to_download = [
        ("parsing_script.py", "/app/task_files/parsing_script.py"),
        ("run_script.sh", "/app/task_files/run_script.sh"),
    ]
    
    for filename, local_path in files_to_download:
        url = f"{REMOTE_BASE_URL}/{instance_id}_{filename}"
        print(f"\n→ Downloading {filename}...")
        print(f"  URL: {url}")
        try:
            urllib.request.urlretrieve(url, local_path)
            # Make run_script.sh executable
            if filename.endswith(".sh"):
                os.chmod(local_path, 0o755)
            print(f"  ✓ Saved to: {local_path}")
        except urllib.error.HTTPError as e:
            print(f"  ✗ Failed to download: HTTP {e.code}")
            return False
        except Exception as e:
            print(f"  ✗ Failed to download: {e}")
            return False
    
    return True

def run_command(cmd, cwd=None, check=True, timeout=None):
    """Run a shell command and return the result."""
    print(f"  → Running: {cmd}")
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=cwd or "/app",
            capture_output=True, text=True, timeout=timeout
        )
        if check and result.returncode != 0:
            print(f"  ⚠ Command returned non-zero: {result.returncode}")
        return result
    except subprocess.TimeoutExpired:
        print(f"  ✗ ERROR: Command timed out after {timeout}s")
        # Return a fake result for timeout
        class TimeoutResult:
            def __init__(self):
                self.stdout = ""
                self.stderr = f"Command timed out after {timeout}s"
                self.returncode = -1
                self.timed_out = True
        return TimeoutResult()

def apply_patch(patch_file, required=True, exit_code=EXIT_ERROR_PATCH_FAILED):
    """Apply a git patch file.
    
    Args:
        patch_file: Path to the patch file
        required: If True, raise exception on failure
        exit_code: Specific exit code to use on failure (default: EXIT_ERROR_PATCH_FAILED)
    """
    print(f"\n{'='*60}")
    print(f"Applying patch: {patch_file}")
    print('='*60)
    
    # Check if patch file exists
    if not os.path.exists(patch_file):
        error_msg = f"Patch file not found: {patch_file}"
        print(f"  ✗ ERROR: {error_msg}")
        if required:
            raise TaskCheckerError(error_msg, EXIT_ERROR_FILE_NOT_FOUND)
        return False
    
    result = run_command(f"git apply -v --ignore-whitespace {patch_file}", check=False)
    if result.returncode == 0:
        print(f"  ✓ Patch applied successfully")
        return True
    else:
        print(f"  stderr: {result.stderr}")
        error_msg = f"Failed to apply patch: {patch_file}"
        print(f"  ✗ ERROR: {error_msg}")
        if required:
            raise TaskCheckerError(error_msg, exit_code)
        return False

def extract_test_file(test_spec):
    """Extract the test file path from a test specification.
    
    Handles different formats:
    - Python: "tests/unit/foo.py::TestClass::test_method" → "tests/unit/foo.py"
    - TypeScript/JS: "test/file.ts | Suite | test name" → "test/file.ts"
    - Go: "TestFunctionName" → "TestFunctionName" (no file, just function name)
    """
    # Check for Python format (contains ::)
    if "::" in test_spec:
        return test_spec.split("::")[0]
    
    # Check for TypeScript/JavaScript format (contains " | ")
    if " | " in test_spec:
        return test_spec.split(" | ")[0].strip()
    
    # For Go or other formats, return as-is (likely just a test function name)
    return test_spec


def extract_unique_test_files(tests):
    """Extract unique test files from a list of test specifications.
    
    Returns a list of unique test file paths, preserving order.
    """
    seen = set()
    unique_files = []
    for test in tests:
        test_file = extract_test_file(test)
        if test_file not in seen:
            seen.add(test_file)
            unique_files.append(test_file)
    return unique_files


def normalize_test_name(test_name):
    """Normalize a test name for matching purposes.
    
    Extracts the core test identifier that can be used for matching.
    Returns a tuple of (file_or_empty, test_parts) for flexible matching.
    """
    # Python format: file::Class::method or file::method
    if "::" in test_name:
        parts = test_name.split("::")
        return (parts[0], parts[1:])
    
    # TypeScript/JS format: file | Suite | test name
    if " | " in test_name:
        parts = [p.strip() for p in test_name.split(" | ")]
        return (parts[0], parts[1:])
    
    # Go or simple format: just the test name
    return ("", [test_name])


def test_name_matches(result_name, expected_name):
    """Check if a result test name matches an expected test name.
    
    Handles different formats and partial matches.
    """
    # Exact match
    if result_name == expected_name:
        return True
    
    # Normalize both names
    result_file, result_parts = normalize_test_name(result_name)
    expected_file, expected_parts = normalize_test_name(expected_name)
    
    # If files are specified in both, they must match (or one is empty)
    if result_file and expected_file:
        # Allow partial file matching (result might have shorter path)
        if not (expected_file.endswith(result_file) or result_file.endswith(expected_file)):
            if result_file != expected_file:
                return False
    
    # Check if the test parts match
    # Result parts should match the end of expected parts (or vice versa)
    if not result_parts or not expected_parts:
        return False
    
    # Check if the last parts match (typically the test method name)
    result_last = result_parts[-1] if result_parts else ""
    expected_last = expected_parts[-1] if expected_parts else ""
    
    if result_last == expected_last:
        return True
    
    # Check all parts match
    if result_parts == expected_parts:
        return True
    
    return False


def filter_results_by_relevant_tests(results, relevant_tests):
    """Filter parsed test results to only include relevant tests.
    
    Args:
        results: Dict with "tests" key containing list of test result dicts
        relevant_tests: List of expected test names to keep
    
    Returns:
        Filtered results dict with only matching tests
    """
    if not results.get("tests"):
        return results
    
    filtered_tests = []
    matched_expected = set()
    
    for test_result in results.get("tests", []):
        result_name = test_result.get("name", "")
        
        for expected_name in relevant_tests:
            if test_name_matches(result_name, expected_name):
                # Rename the test to use the expected name for consistent reporting
                filtered_test = test_result.copy()
                filtered_test["original_name"] = result_name
                filtered_test["name"] = expected_name
                filtered_tests.append(filtered_test)
                matched_expected.add(expected_name)
                break
    
    return {"tests": filtered_tests, "matched_count": len(matched_expected)}


def load_tests_from_file(tests_file):
    """Load test names from a JSON array file."""
    if not os.path.exists(tests_file):
        raise TaskCheckerError(
            f"Tests file not found: {tests_file}",
            EXIT_ERROR_FILE_NOT_FOUND
        )
    
    try:
        with open(tests_file, 'r') as f:
            content = f.read().strip()
        
        if not content:
            raise TaskCheckerError(
                f"Tests file is empty: {tests_file}",
                EXIT_ERROR_FILE_NOT_FOUND
            )
        
        tests = json.loads(content)
        
        if not isinstance(tests, list):
            raise TaskCheckerError(
                f"Tests file must contain a JSON array, got: {type(tests).__name__}",
                EXIT_ERROR_FILE_NOT_FOUND
            )
        
        if len(tests) == 0:
            raise TaskCheckerError(
                f"Tests array is empty in: {tests_file}",
                EXIT_ERROR_FILE_NOT_FOUND
            )
        
        return tests
    except json.JSONDecodeError as e:
        raise TaskCheckerError(
            f"Invalid JSON in tests file {tests_file}: {e}",
            EXIT_ERROR_FILE_NOT_FOUND
        )

def run_tests(tests, output_prefix, timeout=None):
    """Run the specified tests and save stdout/stderr.
    
    Extracts unique test files from the test specifications and passes
    those to run_script.sh instead of the full test array.
    """
    print(f"\n{'='*60}")
    print(f"Running tests ({output_prefix})")
    print('='*60)
    
    run_script = "/app/task_files/run_script.sh"
    if not os.path.exists(run_script):
        raise TaskCheckerError(
            f"run_script.sh not found: {run_script}",
            EXIT_ERROR_FILE_NOT_FOUND
        )
    
    timeout = timeout or DEFAULT_TEST_TIMEOUT
    print(f"  Timeout: {timeout}s")
    
    # Extract unique test files from test specifications
    test_files = extract_unique_test_files(tests)
    print(f"  Extracted {len(test_files)} unique test file(s):")
    for tf in test_files:
        print(f"    - {tf}")
    
    tests_str = ",".join(test_files)
    stdout_file = f"/app/task_files/{output_prefix}_stdout.log"
    stderr_file = f"/app/task_files/{output_prefix}_stderr.log"
    cmd = f"bash {run_script} '{tests_str}'"
    
    result = run_command(cmd, check=False, timeout=timeout)
    
    # Check for timeout
    if hasattr(result, 'timed_out') and result.timed_out:
        raise TaskCheckerError(
            f"Tests timed out after {timeout}s during '{output_prefix}' phase",
            EXIT_ERROR_TESTS_TIMEOUT
        )
    
    with open(stdout_file, 'w') as f:
        f.write(result.stdout)
    with open(stderr_file, 'w') as f:
        f.write(result.stderr)
    
    print(f"  ✓ Saved stdout to: {stdout_file}")
    print(f"  ✓ Saved stderr to: {stderr_file}")
    return stdout_file, stderr_file

def parse_results(stdout_file, stderr_file, output_json):
    """Parse test results using parsing_script.py."""
    print(f"\n{'='*60}")
    print(f"Parsing results → {output_json}")
    print('='*60)
    
    parsing_script = "/app/task_files/parsing_script.py"
    if not os.path.exists(parsing_script):
        raise TaskCheckerError(
            f"parsing_script.py not found: {parsing_script}",
            EXIT_ERROR_FILE_NOT_FOUND
        )
    
    cmd = f"python {parsing_script} {stdout_file} {stderr_file} {output_json}"
    result = run_command(cmd, check=False, timeout=60)
    
    if result.returncode != 0:
        print(f"  ⚠ Parser stderr: {result.stderr}")
        raise TaskCheckerError(
            f"Failed to parse test results: {result.stderr}",
            EXIT_ERROR_PARSE_FAILED
        )
    
    print(f"  ✓ Results parsed successfully")
    return output_json

def load_results(json_file):
    """Load parsed test results from JSON."""
    if not os.path.exists(json_file):
        print(f"  ⚠ Results file not found: {json_file}")
        return {"tests": []}
    with open(json_file, 'r') as f:
        return json.load(f)

def evaluate_results(before_results, after_results, expected_tests):
    """Evaluate if the patch is successful.
    
    Success criteria:
        - Before: FAILED, ERROR, or NOT_FOUND
        - After: PASSED
        
    NOT_FOUND→PASSED is valid because test_patch may introduce new tests
    that don't exist before the patch is applied.
    
    Returns:
        tuple: (success: bool, details: list, exit_code: int)
        
    Exit codes returned:
        EXIT_SUCCESS (0): All tests passed F2P check
        EXIT_TESTS_NOT_FOUND (10): Expected tests not found in AFTER results
        EXIT_TESTS_PASSED_BEFORE (11): Tests passed before golden patch
        EXIT_TESTS_FAILED_AFTER (12): Tests failed after golden patch
        EXIT_FAILURE (1): Generic F2P failure (mixed issues)
    """
    print(f"\n{'='*60}")
    print("EVALUATING RESULTS")
    print('='*60)
    before_status = {t["name"]: t["status"] for t in before_results.get("tests", [])}
    after_status = {t["name"]: t["status"] for t in after_results.get("tests", [])}
    print(f"\nExpected tests to evaluate: {len(expected_tests)}")
    print(f"Tests found in before_results: {len(before_status)}")
    print(f"Tests found in after_results: {len(after_status)}")
    
    all_passed = True
    results_detail = []
    
    # Track specific failure types
    tests_not_found = []
    tests_passed_before = []
    tests_failed_after = []
    
    print("\n" + "-"*60)
    print(f"{'Test Name':<50} | Before → After | Status")
    print("-"*60)
    
    for test in expected_tests:
        before = before_status.get(test, "NOT_FOUND")
        after = after_status.get(test, "NOT_FOUND")
        # Accept FAIL→PASS, ERROR→PASS, or NOT_FOUND→PASS as success
        # NOT_FOUND→PASS is valid when test_patch introduces new tests
        is_success = (before in ["FAILED", "ERROR", "NOT_FOUND"]) and (after == "PASSED")
        status = "✓ OK" if is_success else "✗ FAIL"
        
        if not is_success:
            all_passed = False
            # Categorize the failure type
            if after == "NOT_FOUND":
                # Only count as "not found" if test wasn't found AFTER golden patch
                tests_not_found.append(test)
            elif before == "PASSED":
                tests_passed_before.append(test)
            elif after in ["FAILED", "ERROR"]:
                tests_failed_after.append(test)
        
        short_name = test.split("::")[-1] if "::" in test else test
        if len(short_name) > 45:
            short_name = short_name[:42] + "..."
        print(f"{short_name:<50} | {before:>9} → {after:<6} | {status}")
        results_detail.append({"test": test, "before": before, "after": after, "success": is_success})
    
    # Determine the specific exit code based on failure types
    if all_passed:
        exit_code = EXIT_SUCCESS
    elif tests_not_found and not tests_passed_before and not tests_failed_after:
        # Only NOT_FOUND errors
        exit_code = EXIT_TESTS_NOT_FOUND
    elif tests_passed_before and not tests_not_found and not tests_failed_after:
        # Only PASSED_BEFORE errors
        exit_code = EXIT_TESTS_PASSED_BEFORE
    elif tests_failed_after and not tests_not_found and not tests_passed_before:
        # Only FAILED_AFTER errors
        exit_code = EXIT_TESTS_FAILED_AFTER
    else:
        # Mixed failure types or other cases
        exit_code = EXIT_FAILURE
    
    return all_passed, results_detail, exit_code

def reset_git_state():
    """Reset git tracked files only (no clean)."""
    print("\n→ Resetting git tracked files...")
    run_command("git checkout -- .", check=False)

def ensure_xvfb_running():
    """Ensure Xvfb is running for GUI tests (if available)."""
    import time
    import shutil
    
    print("\n→ Checking for Xvfb (needed for GUI tests)...")
    
    # Check if Xvfb is installed
    if not shutil.which("Xvfb"):
        print("  ℹ Xvfb not installed - skipping (not needed for non-GUI tests)")
        return
    
    print("  → Starting Xvfb...")
    # Kill any existing Xvfb on :99
    subprocess.run("pkill -f 'Xvfb :99' 2>/dev/null || true", shell=True)
    time.sleep(0.5)
    # Clean up stale lock file
    subprocess.run("rm -f /tmp/.X99-lock 2>/dev/null || true", shell=True)
    
    try:
        # Start fresh Xvfb as detached process
        subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1024x768x24"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        time.sleep(2)
        print("  ✓ Xvfb started on :99")
    except Exception as e:
        print(f"  ⚠ Could not start Xvfb: {e}")
        print("    (This is fine for non-GUI tests)")

def revert_patches(test_patch, golden_patch):
    """Revert any previously applied patches to ensure clean state."""
    print("\n" + "="*60)
    print("REVERTING ANY PREVIOUSLY APPLIED PATCHES")
    print("="*60)
    # Try to reverse golden patch first (it's applied after test patch)
    result = run_command(f"git apply -R --check {golden_patch} 2>/dev/null", check=False)
    if result.returncode == 0:
        print(f"  → Reverting: {golden_patch}")
        run_command(f"git apply -R {golden_patch}", check=False)
    else:
        print(f"  → Golden patch not applied (skipping revert)")
    
    # Then try to reverse test patch
    result = run_command(f"git apply -R --check {test_patch} 2>/dev/null", check=False)
    if result.returncode == 0:
        print(f"  → Reverting: {test_patch}")
        run_command(f"git apply -R {test_patch}", check=False)
    else:
        print(f"  → Test patch not applied (skipping revert)")

def main():
    parser = argparse.ArgumentParser(
        description="Evaluate patch success",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python task_files/task_checker.py
  python task_files/task_checker.py '["tests/foo.py::test_one", "tests/foo.py::test_two"]'
  python task_files/task_checker.py --test-patch /app/my.diff --no-cleanup
        """
    )
    parser.add_argument("tests_json", nargs="?", default=None,
                        help="JSON array of test names (overrides --tests-file)")
    parser.add_argument("--instance-id", default=None,
                        help="Instance ID to download parsing_script.py and run_script.sh from remote")
    parser.add_argument("--tests-file", default="/app/task_files/relevant_tests.txt",
                        help="Path to file with JSON array of tests")
    parser.add_argument("--test-patch", default="/app/test_patch_obstructed.diff",
                        help="Path to patch that adds tests")
    parser.add_argument("--golden-patch", default="/app/golden_patch_obstructed.diff",
                        help="Path to patch with implementation")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TEST_TIMEOUT,
                        help=f"Timeout for test execution in seconds (default: {DEFAULT_TEST_TIMEOUT})")
    parser.add_argument("--no-cleanup", action="store_true",
                        help="Don't reset git state after evaluation")
    args = parser.parse_args()
    
    try:
        print("="*60)
        print("TASK CHECKER - PATCH EVALUATION")
        print("="*60)
        
        # Download task files if instance_id is provided
        if args.instance_id:
            if not download_task_files(args.instance_id):
                raise TaskCheckerError(
                    "Failed to download task files",
                    EXIT_ERROR_DOWNLOAD
                )
        
        print(f"\nTest patch: {args.test_patch}")
        print(f"Golden patch: {args.golden_patch}")
        print(f"Test timeout: {args.timeout}s")
        
        # Load tests from argument or file
        if args.tests_json:
            print(f"Tests source: command line argument")
            try:
                tests = json.loads(args.tests_json)
            except json.JSONDecodeError as e:
                raise TaskCheckerError(
                    f"Invalid JSON in tests argument: {e}",
                    EXIT_ERROR_FILE_NOT_FOUND
                )
        else:
            print(f"Tests source: {args.tests_file}")
            tests = load_tests_from_file(args.tests_file)
        
        print(f"\nLoaded {len(tests)} tests to evaluate:")
        for t in tests:
            print(f"  - {t}")
        
        # Step 0: Ensure Xvfb is running (needed for GUI tests)
        ensure_xvfb_running()
        
        # Step 1: Revert any previously applied patches (safe re-run)
        revert_patches(args.test_patch, args.golden_patch)
        
        # Step 2: Reset tracked files only
        reset_git_state()
        
        # Step 3: Apply test patch
        apply_patch(args.test_patch, required=True, exit_code=EXIT_ERROR_TEST_PATCH_FAILED)
        
        # Step 4: Run tests (should fail)
        stdout1, stderr1 = run_tests(tests, "before", timeout=args.timeout)
        
        # Step 5: Parse results
        before_json = "/app/task_files/before_test_results.json"
        parse_results(stdout1, stderr1, before_json)
        before_results_raw = load_results(before_json)
        
        # Step 5b: Filter results to only include relevant tests
        before_results = filter_results_by_relevant_tests(before_results_raw, tests)
        print(f"\n→ Filtered before results: {before_results.get('matched_count', 0)}/{len(tests)} relevant tests matched")
        
        # Step 6: Apply golden patch
        apply_patch(args.golden_patch, required=True, exit_code=EXIT_ERROR_GOLDEN_PATCH_FAILED)
        
        # Step 7: Run tests again (should pass)
        stdout2, stderr2 = run_tests(tests, "after", timeout=args.timeout)
        
        # Step 8: Parse results
        after_json = "/app/task_files/after_test_results.json"
        parse_results(stdout2, stderr2, after_json)
        after_results_raw = load_results(after_json)
        
        # Step 8b: Filter results to only include relevant tests
        after_results = filter_results_by_relevant_tests(after_results_raw, tests)
        print(f"\n→ Filtered after results: {after_results.get('matched_count', 0)}/{len(tests)} relevant tests matched")
        
        # Step 9: Evaluate (using filtered results)
        success, details, exit_code = evaluate_results(before_results, after_results, tests)
        
        print("\n" + "="*60)
        if success:
            print("Overall Result: SUCCESS ✓")
            print("="*60)
            print("All tests failed (or not found) before the golden patch and passed after.")
        else:
            print("Overall Result: FAILURE ✗")
            print("="*60)
            # Show specific error description
            error_desc = ERROR_DESCRIPTIONS.get(exit_code, "Unknown error")
            print(f"Error: {error_desc}")
            print("Some tests did not follow the expected FAIL/NOT_FOUND→PASS pattern.")
            failed_tests = [d for d in details if not d["success"]]
            print(f"\nFailed checks ({len(failed_tests)}):")
            for d in failed_tests:
                print(f"  - {d['test']}: {d['before']} → {d['after']}")
        
        if not args.no_cleanup:
            print("\n→ Cleaning up...")
            reset_git_state()
        
        sys.exit(exit_code)
        
    except TaskCheckerError as e:
        print("\n" + "="*60)
        print(f"ERROR: {e.message}")
        print("="*60)
        print(f"\nExit code: {e.exit_code}")
        if not args.no_cleanup:
            print("\n→ Cleaning up after error...")
            reset_git_state()
        sys.exit(e.exit_code)
        
    except KeyboardInterrupt:
        print("\n\n" + "="*60)
        print("INTERRUPTED: Operation cancelled by user")
        print("="*60)
        sys.exit(130)
        
    except Exception as e:
        print("\n" + "="*60)
        print(f"UNEXPECTED ERROR: {type(e).__name__}: {e}")
        print("="*60)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
