"""
Full dataset-building pipeline for one project.

For every build in builds.csv:
  1. Pick the representative commit (last one in the '#'-separated list)
  2. Extract the diff for that commit (vs. its parent)
  3. Extract ALL test files that exist at that commit (not just changed ones)
  4. Attach the pass/fail labels for that build from exe.csv
  5. Write one JSON record per build to an output file (JSON Lines format)

Usage:
    python build_dataset_for_project.py <project_folder> <repo_subfolder_name> [--limit N]

Example:
    python build_dataset_for_project.py "apache@logging-log4j2" "logging-log4j2" --limit 20
"""
import subprocess
import json
import csv
import sys
import argparse
from pathlib import Path
from collections import defaultdict


def run_git(repo_path, args):
    """Run a git command, safely decoding output regardless of OS/encoding."""
    result = subprocess.run(
        ["git", "-C", str(repo_path)] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",   # force UTF-8 decoding
        errors="replace",   # never crash on a weird byte -- swap it for a placeholder instead
    )
    if result.returncode != 0:
        return None  # commit might not exist locally, or some other git issue -- skip, don't crash
    return result.stdout


def get_commit_diff(repo_path, commit_sha):
    commit_line = run_git(repo_path, ["rev-list", "--parents", "-n", "1", commit_sha])
    if not commit_line:
        return None
    commit_parts = commit_line.strip().split()
    if len(commit_parts) > 1:
        return run_git(repo_path, ["diff", commit_parts[1], commit_sha, "--unified=3"])
    return run_git(repo_path, ["diff-tree", "--root", "-p", "--no-commit-id", "-r", commit_sha])


def get_changed_files(repo_path, commit_sha):
    commit_line = run_git(repo_path, ["rev-list", "--parents", "-n", "1", commit_sha])
    if not commit_line:
        return []
    commit_parts = commit_line.strip().split()
    if len(commit_parts) > 1:
        out = run_git(repo_path, ["diff", "--name-only", commit_parts[1], commit_sha])
    else:
        out = run_git(repo_path, ["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", commit_sha])
    return [f for f in out.strip().split("\n") if f] if out else []


def get_file_content_at_commit(repo_path, commit_sha, file_path):
    return run_git(repo_path, ["show", f"{commit_sha}:{file_path}"])


def first_present(row, *column_names):
    """Return the first non-empty value from equivalent column names."""
    for column_name in column_names:
        value = row.get(column_name)
        if value not in (None, ""):
            return value
    return None


def pick_representative_commit(commits_field):
    """Builds can bundle multiple commits, separated by '#'. We use the last one
    (the most recent -- i.e. the actual code state at build time)."""
    commits = commits_field.split("#")
    return commits[-1].strip()


def load_id_map(project_folder):
    """Read id_map.csv and return a dict: numeric_id_str -> file_path."""
    id_map_path = project_folder / "id_map.csv"
    id_to_path = {}
    if id_map_path.exists():
        with open(id_map_path, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # id_map.csv has columns: key (file path), value (numeric id)
                id_to_path[row["value"]] = row["key"]
        print(f"Loaded {len(id_to_path)} entries from id_map.csv")
    else:
        print(f"WARNING: id_map.csv not found at {id_map_path} — test names will be numeric IDs")
    return id_to_path


def load_exe_outcomes(project_folder, id_to_path):
    """Read exe.csv once, group test outcomes by build id.
    Resolves numeric test IDs to file paths using id_to_path."""
    exe_path = project_folder / "exe.csv"
    outcomes_by_build = defaultdict(list)
    with open(exe_path, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # v2 uses 'build', older versions use 'travisBuildId' or 'tr_build_id'
            build_id = row.get("build") or row.get("travisBuildId") or row.get("tr_build_id")
            # v2 uses 'test' (numeric id) + 'verdict' + 'duration'
            # older versions use 'testName', 'failures', 'errors', 'skipped'
            test_id = row.get("test")
            test_name = id_to_path.get(test_id, test_id) if test_id else row.get("testName")
            verdict_raw = row.get("verdict")
            # verdict: 0 = passed, 1 = failed
            passed = (verdict_raw == "0") if verdict_raw is not None else None
            outcomes_by_build[build_id].append({
                "testId": test_id,
                "testName": test_name,
                "verdict": verdict_raw,
                "passed": passed,
                "duration": row.get("duration"),
            })
    return outcomes_by_build


def build_dataset(project_folder, repo_subfolder, limit=None, output_path=None):
    project_folder = Path(project_folder)
    repo_path = project_folder / repo_subfolder
    builds_csv = project_folder / "builds.csv"
    out_path = Path(output_path) if output_path else project_folder / "extracted_dataset.jsonl"

    print(f"Loading id_map.csv ...")
    id_to_path = load_id_map(project_folder)

    print(f"Loading test outcomes from exe.csv ...")
    outcomes_by_build = load_exe_outcomes(project_folder, id_to_path)

    print(f"Reading builds.csv ...")
    with open(builds_csv, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if limit:
        rows = rows[:limit]
        print(f"Limiting to first {limit} builds for this test run.")

    written = 0
    skipped = 0
    skip_reasons = defaultdict(int)

    # --- diagnostic block: run once before the main loop, prints exactly what's wrong ---
    print(f"\n--- DIAGNOSTIC ---")
    print(f"repo_path resolved to: {repo_path.resolve()}")
    print(f"repo_path exists: {repo_path.exists()}")
    print(f".git exists inside it: {(repo_path / '.git').exists()}")
    if rows:
        print(f"builds.csv columns found: {list(rows[0].keys())}")
        first_commits = first_present(rows[0], "commits", "git_all_built_commits")
        print(f"First row's commit-list value: {first_commits!r}")
        test_sha = pick_representative_commit(first_commits) if first_commits else None
        if test_sha:
            test_check = run_git(repo_path, ["cat-file", "-e", test_sha])
            check_result = subprocess.run(
                ["git", "-C", str(repo_path), "cat-file", "-e", test_sha],
                capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
            print(f"Does git recognize commit {test_sha}? return code: {check_result.returncode} (0 = yes, found)")
            if check_result.returncode != 0:
                print(f"  git error message: {check_result.stderr.strip()}")
    print(f"--- END DIAGNOSTIC ---\n")
    # --- end diagnostic block ---

    with open(out_path, "w", encoding="utf-8") as out_f:
        for i, row in enumerate(rows):
            build_id = first_present(row, "id", "tr_build_id")
            commits_field = first_present(row, "commits", "git_all_built_commits")
            if not commits_field:
                skipped += 1
                skip_reasons["empty commits field"] += 1
                continue

            commit_sha = pick_representative_commit(commits_field)

            diff_text = get_commit_diff(repo_path, commit_sha)
            if diff_text is None:
                skipped += 1
                skip_reasons["commit not found by git"] += 1
                continue  # commit not found in local repo -- skip, don't crash

            changed_files = get_changed_files(repo_path, commit_sha)
            record = {
                "build_id": build_id,
                "commit_sha": commit_sha,
                "started_at": first_present(row, "started_at", "gh_build_started_at"),
                "diff_text": diff_text,
                "changed_files": changed_files,
                "test_outcomes": outcomes_by_build.get(build_id, []),
            }
            out_f.write(json.dumps(record) + "\n")
            written += 1

            if (i + 1) % 10 == 0:
                print(f"  processed {i + 1}/{len(rows)} builds...")

    print(f"\nDone. Wrote {written} records to {out_path}")
    print(f"Skipped {skipped} builds. Breakdown:")
    for reason, count in skip_reasons.items():
        print(f"  - {reason}: {count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("project_folder", help="Path to the project's folder (containing builds.csv, exe.csv, and the repo subfolder)")
    parser.add_argument("repo_subfolder", help="Name of the repo subfolder inside project_folder (the one with .git in it)")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N builds (for testing)")
    parser.add_argument("--output", help="Output JSONL path; use a separate path for test runs")
    args = parser.parse_args()

    build_dataset(args.project_folder, args.repo_subfolder, args.limit, args.output)