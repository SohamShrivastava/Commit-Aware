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
import csv
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
    builds_path = project_folder / "builds.csv"
    out_path = project_folder / "unique_tests.jsonl"

    print(f"Scanning build metadata under {project_folder / 'analysis'} for test files...")

    # Use TCP-CI's explicit TEST/SRC label rather than guessing from filenames.
    test_latest_commit = {}
    total_builds = 0
    missing_metadata = 0

    with open(builds_path, encoding="utf-8", errors="replace", newline="") as builds_file:
        for build in csv.DictReader(builds_file):
            total_builds += 1
            build_id = build.get("id") or build.get("tr_build_id")
            commits = build.get("commits") or build.get("git_all_built_commits") or ""
            commit = commits.split("#")[-1].strip()
            metadata_path = project_folder / "analysis" / str(build_id) / "metadata.csv"

            if not commit or not metadata_path.exists():
                missing_metadata += 1
                continue

            with open(metadata_path, encoding="utf-8", errors="replace", newline="") as metadata_file:
                for row in csv.DictReader(metadata_file):
                    if row.get("EntityType", "").upper() == "TEST" and row.get("FilePath"):
                        test_latest_commit[row["FilePath"]] = commit

    print(f"  Found {len(test_latest_commit)} unique test files across {total_builds} builds")
    if missing_metadata:
        print(f"  WARNING: {missing_metadata} builds had no commit or metadata and were skipped")

    # Pass 2: extract source code for each test at its latest commit
    written = 0
    failed = 0

    with open(out_path, "w", encoding="utf-8") as out_f:
        for i, (test_path, commit) in enumerate(test_latest_commit.items()):
            source = run_git(repo_path, ["cat-file", "-p", f"{commit}:{test_path}"])

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
