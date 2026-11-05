"""
Count builds and failed builds for every project folder under the TCP-CI 'datasets' directory.

Real column names in this dataset version:
    builds.csv : id, commits, started_at
    exe.csv    : test, build, job, verdict, duration
    dataset.csv: Build, Test, <feature columns...>   (only fault-revealing builds)

A build counts as FAILED if any row of exe.csv for it has a verdict that is not a
'pass' value. The script prints every distinct verdict value it sees so you can
confirm the interpretation is right.

Usage:
    python count_failures.py "TCP-CI-dataset-v2\\datasets"
"""
import csv
import sys
from collections import Counter
from pathlib import Path

csv.field_size_limit(10**9)

PASS_VALUES = {"0", "0.0", "pass", "passed", "success", "ok", "true", ""}
verdict_seen = Counter()


def find_col(header, candidates):
    lowered = {h.lower(): h for h in header}
    for c in candidates:
        if c.lower() in lowered:
            return lowered[c.lower()]
    return None


def count_project(folder):
    res = {"builds": None, "failed_builds_exe": None, "builds_in_dataset_csv": None, "note": ""}

    builds_csv = folder / "builds.csv"
    if builds_csv.exists():
        with open(builds_csv, encoding="utf-8", errors="replace") as f:
            res["builds"] = sum(1 for _ in csv.DictReader(f))

    exe_csv = folder / "exe.csv"
    if exe_csv.exists():
        failed = set()
        with open(exe_csv, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            bcol = find_col(header, ["build", "buildId", "travisBuildId"])
            vcol = find_col(header, ["verdict"])
            if bcol is None or vcol is None:
                res["note"] += f"exe.csv columns: {header} "
            else:
                for row in reader:
                    v = str(row[vcol]).strip().lower()
                    verdict_seen[v] += 1
                    if v not in PASS_VALUES:
                        failed.add(row[bcol])
                res["failed_builds_exe"] = len(failed)

    ds_csv = folder / "dataset.csv"
    if ds_csv.exists():
        ids = set()
        with open(ds_csv, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            bcol = find_col(header, ["Build", "buildId", "tr_build_id"])
            if bcol is None:
                res["note"] += f"dataset.csv columns (first 10): {header[:10]} "
            else:
                for row in reader:
                    ids.add(row[bcol])
                res["builds_in_dataset_csv"] = len(ids)
    return res


def main(datasets_dir):
    datasets_dir = Path(datasets_dir)
    rows = []
    for folder in sorted(p for p in datasets_dir.iterdir() if p.is_dir()):
        rows.append((folder.name, count_project(folder)))

    rows.sort(key=lambda x: (x[1]["failed_builds_exe"] or 0), reverse=True)
    print(f"{'project':40} {'builds':>7} {'failed(exe)':>12} {'in dataset.csv':>15}")
    for name, r in rows:
        print(f"{name:40} {str(r['builds']):>7} {str(r['failed_builds_exe']):>12} {str(r['builds_in_dataset_csv']):>15}  {r['note']}")

    print("\nDistinct verdict values seen in exe.csv (value: row count):")
    for v, c in verdict_seen.most_common(10):
        print(f"  {v!r}: {c}")


if __name__ == "__main__":
    main(sys.argv[1])
