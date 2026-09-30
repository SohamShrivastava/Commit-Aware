"""
Step 1: Extract all unique test files across all builds for a project.

For each unique test file path, grabs the actual source code from the Git repo
at the LATEST commit where that test exists. Outputs unique_tests.jsonl.

Usage:
    python -m src.offline_indexing.extract_unique_tests log4j2
    python -m src.offline_indexing.extract_unique_tests shardingsphere
"""
import json
import subprocess
import argparse
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.config import PROJECTS


def run_git(repo_path, args):
    """Run a git command, safely decoding output."""
    result = subprocess.run(
        ["git", "-C", str(repo_path)] + args,
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        return None
    return result.stdout


def extract_unique_tests(project_key):
    proj = PROJECTS[project_key]
    project_folder = proj["folder"]
    repo_path = project_folder / proj["repo_subfolder"]
    dataset_path = project_folder / "extracted_dataset.jsonl"
    out_path = project_folder / "unique_tests.jsonl"

    print(f"Scanning {dataset_path} for unique test files...")

    # Pass 1: find all unique test paths and the LATEST commit for each
    # (latest = last build in chronological order where the test appeared)
    test_latest_commit = {}   # test_path -> (commit_sha, build_index)
    total_builds = 0

    with open(dataset_path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            record = json.loads(line)
            commit = record["commit_sha"]
            for test_path in record["all_test_files_at_commit"]:
                test_latest_commit[test_path] = commit
            total_builds = i + 1

    print(f"  Found {len(test_latest_commit)} unique test files across {total_builds} builds")

    # Pass 2: extract source code for each test at its latest commit
    written = 0
    failed = 0

    with open(out_path, "w", encoding="utf-8") as out_f:
        for i, (test_path, commit) in enumerate(test_latest_commit.items()):
            source = run_git(repo_path, ["show", f"{commit}:{test_path}"])

            if source is None:
                failed += 1
                continue

            record = {
                "test_path": test_path,
                "from_commit": commit,
                "char_count": len(source),
                "source_code": source,
            }
            out_f.write(json.dumps(record) + "\n")
            written += 1

            if (i + 1) % 100 == 0:
                print(f"  extracted {i + 1}/{len(test_latest_commit)} test files...")

    print(f"\nDone. Wrote {written} unique tests to {out_path}")
    if failed:
        print(f"  ({failed} tests could not be read from git — likely deleted in latest commits)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("project", choices=list(PROJECTS.keys()), help="Project key")
    args = parser.parse_args()
    extract_unique_tests(args.project)
