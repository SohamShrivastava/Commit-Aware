"""
Step 1: Chronological 3-Way Dataset Slicing.

Divides each project's complete_dataset.jsonl into three strictly
chronological, non-overlapping slices based on build timestamps:

    Slice 1 — Parameter Tuning  (earliest 25% of builds)
    Slice 2 — Calibration       (middle  35% of builds)
    Slice 3 — Evaluation        (latest  40% of builds)

Chronological ordering prevents temporal data leakage that would
invalidate any ML evaluation in a CI/CD context.

Outputs:
    splits.json   — build IDs and date ranges for each slice & project.
    <project>/slice_1_tune.jsonl
    <project>/slice_2_cal.jsonl
    <project>/slice_3_eval.jsonl

Usage:
    python -m src.data_pipeline.split_dataset
    python -m src.data_pipeline.split_dataset --tune-frac 0.2 --cal-frac 0.3
"""
import json
import argparse
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.config import PROJECTS, DATASETS_DIR


# ── Default split fractions (must sum to 1.0) ──
DEFAULT_TUNE_FRAC = 0.25
DEFAULT_CAL_FRAC = 0.35
# Eval fraction is the remainder: 1.0 - tune - cal = 0.40


def parse_timestamp(ts_str):
    """Parse a timestamp string from complete_dataset.jsonl.

    Handles formats:
        2019-09-02 21:57:11+00:00
        2019-09-02T21:57:11+00:00
        2019-09-02T21:57:11Z
    Falls back to string sort if parsing fails.
    """
    if not ts_str:
        return None
    ts_str = ts_str.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    # Handle trailing 'Z'
    if ts_str.endswith("Z"):
        try:
            return datetime.strptime(ts_str[:-1] + "+0000", "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            pass
    return None


def load_builds_with_timestamps(dataset_path):
    """Read complete_dataset.jsonl, return list of (line_index, record_metadata).

    Only the lightweight metadata is kept in memory — not the full diff_text.
    """
    builds = []
    with open(dataset_path, encoding="utf-8", errors="replace") as f:
        for idx, line in enumerate(f):
            rec = json.loads(line)
            ts = parse_timestamp(rec.get("started_at"))
            builds.append({
                "line_index": idx,
                "build_id": rec["build_id"],
                "commit_sha": rec["commit_sha"],
                "started_at_raw": rec.get("started_at", ""),
                "started_at": ts,
                "n_tests": len(rec.get("test_outcomes", [])),
                "n_failures": sum(
                    1 for t in rec.get("test_outcomes", [])
                    if str(t.get("verdict")) in ("1", "2", "3")
                ),
            })
    return builds


def split_builds(builds, tune_frac, cal_frac):
    """Split a sorted list of builds into three chronological slices.

    Returns (slice_1_tune, slice_2_cal, slice_3_eval) — each is a list
    of build metadata dicts.
    """
    # Sort by parsed timestamp (builds without timestamps go to the front)
    builds_sorted = sorted(
        builds,
        key=lambda b: (b["started_at"] is None, b["started_at"] or datetime.min, b["line_index"])
    )

    n = len(builds_sorted)
    n_tune = max(1, int(n * tune_frac))
    n_cal = max(1, int(n * cal_frac))
    # Ensure eval gets at least 1 build
    if n_tune + n_cal >= n:
        n_cal = n - n_tune - 1
        if n_cal < 1:
            n_tune = n - 2
            n_cal = 1

    slice_1 = builds_sorted[:n_tune]
    slice_2 = builds_sorted[n_tune:n_tune + n_cal]
    slice_3 = builds_sorted[n_tune + n_cal:]

    return slice_1, slice_2, slice_3


def write_slice_file(source_path, output_path, line_indices):
    """Extract specific lines from the source JSONL and write to output.

    Uses a set lookup on line indices for O(1) membership checks during
    a single sequential pass over the (potentially multi-GB) source file.
    """
    target_set = set(line_indices)
    written = 0
    with open(source_path, encoding="utf-8", errors="replace") as src, \
         open(output_path, "w", encoding="utf-8") as dst:
        for idx, line in enumerate(src):
            if idx in target_set:
                dst.write(line)
                written += 1
    return written


def slice_summary(slice_builds, label):
    """Return a summary dict for a slice."""
    if not slice_builds:
        return {"label": label, "count": 0}
    timestamps = [b["started_at_raw"] for b in slice_builds if b["started_at_raw"]]
    build_ids = [b["build_id"] for b in slice_builds]
    total_tests = sum(b["n_tests"] for b in slice_builds)
    total_failures = sum(b["n_failures"] for b in slice_builds)
    return {
        "label": label,
        "count": len(slice_builds),
        "build_ids": build_ids,
        "date_range": {
            "earliest": timestamps[0] if timestamps else None,
            "latest": timestamps[-1] if timestamps else None,
        },
        "total_test_executions": total_tests,
        "total_failures": total_failures,
    }


def run_split(tune_frac=DEFAULT_TUNE_FRAC, cal_frac=DEFAULT_CAL_FRAC):
    """Run the 3-way chronological split for all configured projects."""
    eval_frac = round(1.0 - tune_frac - cal_frac, 4)
    assert eval_frac > 0, f"Fractions must sum to < 1.0 (eval gets remainder). Got eval_frac={eval_frac}"

    print(f"Split ratios: Tune={tune_frac:.0%}  Cal={cal_frac:.0%}  Eval={eval_frac:.0%}")
    print("=" * 70)

    global_summary = {
        "split_fractions": {
            "tune": tune_frac,
            "calibration": cal_frac,
            "evaluation": eval_frac,
        },
        "projects": {},
    }

    for project_key, proj_cfg in PROJECTS.items():
        project_folder = proj_cfg["folder"]
        dataset_path = project_folder / "complete_dataset.jsonl"

        if not dataset_path.exists():
            print(f"\n[{project_key}] SKIP -- {dataset_path} not found.")
            continue

        print(f"\n[{project_key}] Loading builds from {dataset_path.name}...")
        builds = load_builds_with_timestamps(dataset_path)
        print(f"  Total builds: {len(builds)}")

        no_ts = sum(1 for b in builds if b["started_at"] is None)
        if no_ts:
            print(f"  WARNING: {no_ts} builds have unparseable timestamps -- sorted to front")

        slice_1, slice_2, slice_3 = split_builds(builds, tune_frac, cal_frac)

        # Write slice files
        slices = [
            ("slice_1_tune.jsonl", slice_1, "Parameter Tuning (Slice 1)"),
            ("slice_2_cal.jsonl", slice_2, "Calibration (Slice 2)"),
            ("slice_3_eval.jsonl", slice_3, "Evaluation (Slice 3)"),
        ]
        project_summary = {}
        for filename, slice_builds, label in slices:
            out_path = project_folder / filename
            line_indices = [b["line_index"] for b in slice_builds]
            written = write_slice_file(dataset_path, out_path, line_indices)
            summary = slice_summary(slice_builds, label)
            project_summary[filename.replace(".jsonl", "")] = summary

            print(f"  {label}: {len(slice_builds)} builds "
                  f"({summary['date_range']['earliest']} -> {summary['date_range']['latest']}) "
                  f"-> {out_path.name}  [{written} records written]")
            print(f"    Total test executions: {summary['total_test_executions']:,}  "
                  f"Failures: {summary['total_failures']:,}")

        global_summary["projects"][project_key] = project_summary

    # Write global splits.json
    splits_path = DATASETS_DIR / "splits.json"
    with open(splits_path, "w", encoding="utf-8") as f:
        json.dump(global_summary, f, indent=2, default=str, ensure_ascii=False)
    print(f"\n{'=' * 70}")
    print(f"Wrote split summary to {splits_path}")

    return global_summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tune-frac", type=float, default=DEFAULT_TUNE_FRAC,
                        help=f"Fraction of builds for parameter tuning (default: {DEFAULT_TUNE_FRAC})")
    parser.add_argument("--cal-frac", type=float, default=DEFAULT_CAL_FRAC,
                        help=f"Fraction of builds for calibration (default: {DEFAULT_CAL_FRAC})")
    args = parser.parse_args()

    run_split(tune_frac=args.tune_frac, cal_frac=args.cal_frac)
