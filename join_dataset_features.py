"""Join TCP-CI's per-test feature rows to extracted build records.

Usage:
    python join_dataset_features.py <project_folder> [--input FILE] [--output FILE] [--limit N]

The output keeps one record per build and adds a `features` object to each
test outcome. Tests without a matching dataset.csv row receive `features: null`.
"""
import argparse
import csv
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


def create_feature_index(dataset_path, database_path):
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            "CREATE TABLE features ("
            "build_id TEXT NOT NULL, "
            "test_id TEXT NOT NULL, "
            "payload TEXT NOT NULL, "
            "PRIMARY KEY (build_id, test_id))"
        )

        with dataset_path.open(encoding="utf-8", errors="replace", newline="") as feature_file:
            reader = csv.DictReader(feature_file)
            if not reader.fieldnames or not {"Build", "Test"}.issubset(reader.fieldnames):
                raise ValueError("dataset.csv must contain Build and Test columns")

            feature_columns = [
                column for column in reader.fieldnames if column not in {"Build", "Test"}
            ]
            insert_rows = []
            feature_count = 0

            for row in reader:
                payload = json.dumps(
                    {column: row[column] for column in feature_columns},
                    ensure_ascii=False,
                )
                insert_rows.append((row["Build"], row["Test"], payload))
                if len(insert_rows) >= 1000:
                    connection.executemany(
                        "INSERT INTO features VALUES (?, ?, ?)", insert_rows
                    )
                    feature_count += len(insert_rows)
                    insert_rows.clear()

            if insert_rows:
                connection.executemany("INSERT INTO features VALUES (?, ?, ?)", insert_rows)
                feature_count += len(insert_rows)

        connection.commit()

    return feature_count, feature_columns


def join_features(project_folder, input_path=None, output_path=None, limit=None):
    project_folder = Path(project_folder)
    input_path = Path(input_path) if input_path else project_folder / "extracted_dataset_joined.jsonl"
    output_path = Path(output_path) if output_path else project_folder / "complete_dataset.jsonl"
    dataset_path = project_folder / "dataset.csv"

    if not input_path.exists():
        raise FileNotFoundError(f"Input build data not found: {input_path}")
    if not dataset_path.exists():
        raise FileNotFoundError(f"TCP-CI feature data not found: {dataset_path}")
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Input and output must be different files")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tcpci-feature-join-") as temp_dir:
        database_path = Path(temp_dir) / "features.sqlite"
        feature_count, feature_columns = create_feature_index(dataset_path, database_path)
        print(f"Loaded {feature_count} feature rows with {len(feature_columns)} feature columns")

        build_count = 0
        outcome_count = 0
        matched_count = 0
        missing_count = 0

        with closing(sqlite3.connect(database_path)) as connection:
            with input_path.open(encoding="utf-8", errors="replace") as input_file, \
                    output_path.open("w", encoding="utf-8") as output_file:
                for line in input_file:
                    if limit is not None and build_count >= limit:
                        break

                    record = json.loads(line)
                    build_id = str(record["build_id"])
                    features_by_test = {
                        test_id: json.loads(payload)
                        for test_id, payload in connection.execute(
                            "SELECT test_id, payload FROM features WHERE build_id = ?",
                            (build_id,),
                        )
                    }

                    for outcome in record.get("test_outcomes", []):
                        test_id = outcome.get("testId")
                        features = features_by_test.get(str(test_id)) if test_id is not None else None
                        outcome["features"] = features
                        outcome_count += 1
                        if features is None:
                            missing_count += 1
                        else:
                            matched_count += 1

                    output_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                    build_count += 1

                    if build_count % 100 == 0:
                        print(f"  joined {build_count} builds...")

    print(f"Wrote {build_count} builds to {output_path}")
    print(f"Test results with features: {matched_count}")
    print(f"Test results without features: {missing_count} of {outcome_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_folder", help="Folder containing dataset.csv and extracted build data")
    parser.add_argument("--input", help="Input extracted_dataset_joined.jsonl path")
    parser.add_argument("--output", help="Output path; defaults to complete_dataset.jsonl")
    parser.add_argument("--limit", type=int, help="Process only the first N builds for a test run")
    args = parser.parse_args()
    join_features(args.project_folder, args.input, args.output, args.limit)