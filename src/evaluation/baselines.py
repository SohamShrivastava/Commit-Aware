"""
Step 2: Test Selection Baselines.

Implements three baseline test selection strategies that the commit-aware
LLM approach must beat:

    Baseline A — Run All Tests (100% recall, 0% savings)
    Baseline B — File Path & Lexical Heuristic (module/directory matching)
    Baseline C — TCP-CI Historical ML Ranker (152-feature Random Forest)

Each baseline exposes a `select(build_record) -> dict` interface returning:
    {
        "selected_tests":  [list of test names],
        "skipped_tests":   [list of test names],
        "scores":          {test_name: relevance_score},  # optional
    }

Usage:
    python -m src.evaluation.baselines log4j2 --slice slice_3_eval.jsonl
    python -m src.evaluation.baselines shardingsphere --slice slice_3_eval.jsonl
"""
import json
import argparse
import sys
import os
import re
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.config import PROJECTS


# ─────────────────────────────────────────────────────────
#  Baseline A: Run All Tests
# ─────────────────────────────────────────────────────────

class RunAllBaseline:
    """Trivially selects every test. Perfect recall, zero savings."""

    name = "RunAll"

    def select(self, build_record):
        tests = [t["testName"] for t in build_record.get("test_outcomes", []) if t.get("testName")]
        return {
            "selected_tests": tests,
            "skipped_tests": [],
            "scores": {t: 1.0 for t in tests},
        }


# ─────────────────────────────────────────────────────────
#  Baseline B: File Path & Lexical Heuristic
# ─────────────────────────────────────────────────────────

class FilePathHeuristicBaseline:
    """Selects tests that share directory prefixes, module names, or
    lexical patterns with the files changed in the commit.

    Heuristic rules (any match → select):
      1. Test path shares a top-level module directory with a changed file.
      2. Test filename contains a substring of the changed filename (or vice versa),
         after stripping 'Test', 'IT', 'Spec' suffixes.
      3. Test is in the same immediate parent directory as a changed file.

    Tests with no heuristic match are skipped.
    """

    name = "FilePathHeuristic"

    @staticmethod
    def _extract_module(path):
        """Extract the top-level module directory from a path.
        e.g. 'log4j-core/src/test/...' → 'log4j-core'
        """
        parts = Path(path).parts
        return parts[0] if parts else ""

    @staticmethod
    def _extract_stem(path):
        """Extract filename stem, stripping common test suffixes.
        e.g. 'FooBarTest.java' → 'foobar'
        """
        name = Path(path).stem
        # Remove common test suffixes
        name = re.sub(r'(Test|Tests|IT|Spec|TestCase)$', '', name, flags=re.IGNORECASE)
        return name.lower()

    @staticmethod
    def _parent_dir(path):
        """Return the immediate parent directory."""
        return str(Path(path).parent)

    def select(self, build_record):
        changed_files = build_record.get("changed_files", [])
        tests = [t["testName"] for t in build_record.get("test_outcomes", []) if t.get("testName")]

        if not changed_files:
            # No changed files means we can't infer anything — run all (safe fallback)
            return {
                "selected_tests": tests,
                "skipped_tests": [],
                "scores": {t: 0.5 for t in tests},
            }

        # Precompute changed-file metadata
        changed_modules = {self._extract_module(f) for f in changed_files}
        changed_stems = {self._extract_stem(f) for f in changed_files}
        changed_parents = {self._parent_dir(f) for f in changed_files}

        selected = []
        skipped = []
        scores = {}

        for test in tests:
            score = 0.0
            test_module = self._extract_module(test)
            test_stem = self._extract_stem(test)
            test_parent = self._parent_dir(test)

            # Rule 1: same top-level module
            if test_module in changed_modules:
                score = max(score, 0.6)

            # Rule 2: lexical stem overlap
            for cs in changed_stems:
                if cs and test_stem and (cs in test_stem or test_stem in cs):
                    score = max(score, 0.8)
                    break

            # Rule 3: same parent directory
            if test_parent in changed_parents:
                score = max(score, 0.9)

            scores[test] = score
            if score > 0:
                selected.append(test)
            else:
                skipped.append(test)

        return {
            "selected_tests": selected,
            "skipped_tests": skipped,
            "scores": scores,
        }


# ─────────────────────────────────────────────────────────
#  Baseline C: TCP-CI Historical ML Ranker
# ─────────────────────────────────────────────────────────

class TCPCIHistoricalBaseline:
    """Uses the precomputed 152 TCP-CI features (prior failure rate,
    code churn, transition age, test execution time, etc.) to rank
    tests using a scikit-learn GradientBoostedClassifier.

    Training: Fits on a training slice (Slice 1) where the label is
    whether the test failed (verdict == "1").

    Selection: At inference time, predicts P(failure) for each test and
    selects the top-k fraction, or all tests above a threshold.
    """

    name = "TCPCIHistorical"

    # Key features known to be predictive in the TCP-CI literature
    PRIORITY_FEATURES = [
        "REC_Age", "REC_TotFails", "REC_AvgExeTime",
        "DET_NumRuns", "DET_NumFails", "DET_AvgExeTime",
        "COV_NumDeps", "COV_SrcChgNum", "COV_TstChgNum",
        "HIS_NumTrans", "HIS_TransAge",
    ]

    def __init__(self, select_fraction=0.5, threshold=None):
        """
        Args:
            select_fraction: Fraction of tests to select (ranked by P(failure)).
                             Used when threshold is not set.
            threshold: If set, selects all tests with P(failure) >= threshold
                       instead of using select_fraction.
        """
        self.select_fraction = select_fraction
        self.threshold = threshold
        self.model = None
        self.feature_names = None
        self._is_fitted = False

    def fit(self, training_records):
        """Train the model on a list of build records from the tuning slice.

        Each test outcome with non-null features becomes a training sample.
        The label is 1 if verdict == "1" (failure), 0 otherwise.
        """
        try:
            from sklearn.ensemble import GradientBoostingClassifier
        except ImportError:
            print("ERROR: scikit-learn is required for Baseline C. Install with: pip install scikit-learn")
            sys.exit(1)

        X_rows = []
        y_rows = []

        # Determine feature names from the first record with features
        feature_names = None
        for rec in training_records:
            for outcome in rec.get("test_outcomes", []):
                feats = outcome.get("features")
                if feats:
                    feature_names = sorted(feats.keys())
                    break
            if feature_names:
                break

        if not feature_names:
            print("  WARNING: No feature data found in training records. "
                  "Baseline C will use a dummy model.")
            self.feature_names = []
            self._is_fitted = False
            return

        self.feature_names = feature_names
        print(f"  Training Baseline C with {len(feature_names)} features...")

        for rec in training_records:
            for outcome in rec.get("test_outcomes", []):
                feats = outcome.get("features")
                if not feats:
                    continue
                row = []
                for fname in feature_names:
                    val = feats.get(fname)
                    try:
                        row.append(float(val) if val is not None else 0.0)
                    except (ValueError, TypeError):
                        row.append(0.0)
                X_rows.append(row)
                y_rows.append(1 if outcome.get("verdict") == "1" else 0)

        if len(X_rows) < 10:
            print(f"  WARNING: Only {len(X_rows)} training samples -- Baseline C may be unreliable.")

        print(f"  Training samples: {len(X_rows)}, Positive rate: {sum(y_rows)/max(1,len(y_rows)):.4f}")

        if len(set(y_rows)) < 2:
            print(f"  WARNING: Only {len(set(y_rows))} class(es) present in training labels ({set(y_rows)}). "
                  "Cannot train binary classifier without at least 2 classes. "
                  "Baseline C will fallback to safe Run All behavior.")
            self._is_fitted = False
            return

        self.model = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            subsample=0.8,
            random_state=42,
        )
        self.model.fit(X_rows, y_rows)
        self._is_fitted = True
        print("  Baseline C model trained successfully.")

    def select(self, build_record):
        tests = []
        test_features = []

        for outcome in build_record.get("test_outcomes", []):
            name = outcome.get("testName")
            if not name:
                continue
            tests.append(name)
            feats = outcome.get("features")
            if feats and self._is_fitted:
                row = []
                for fname in self.feature_names:
                    val = feats.get(fname)
                    try:
                        row.append(float(val) if val is not None else 0.0)
                    except (ValueError, TypeError):
                        row.append(0.0)
                test_features.append(row)
            else:
                test_features.append(None)

        # If model isn't fitted, fall back to Run All
        if not self._is_fitted:
            return {
                "selected_tests": tests,
                "skipped_tests": [],
                "scores": {t: 0.5 for t in tests},
            }

        scores = {}
        for i, test in enumerate(tests):
            if test_features[i] is not None:
                prob = self.model.predict_proba([test_features[i]])[0]
                # Index 1 = probability of failure
                p_fail = prob[1] if len(prob) > 1 else prob[0]
                scores[test] = float(p_fail)
            else:
                # No features → conservative: assume relevant
                scores[test] = 1.0

        # Select based on threshold or fraction
        if self.threshold is not None:
            selected = [t for t in tests if scores[t] >= self.threshold]
            skipped = [t for t in tests if scores[t] < self.threshold]
        else:
            ranked = sorted(tests, key=lambda t: scores[t], reverse=True)
            n_select = max(1, int(len(ranked) * self.select_fraction))
            selected = ranked[:n_select]
            skipped = ranked[n_select:]

        return {
            "selected_tests": selected,
            "skipped_tests": skipped,
            "scores": scores,
        }


# ─────────────────────────────────────────────────────────
#  Evaluation Harness
# ─────────────────────────────────────────────────────────

def evaluate_baseline(baseline, build_records, label=""):
    """Run a baseline on a list of build records and compute aggregate metrics.

    Returns a dict with:
        - total_builds
        - total_tests, total_selected, total_skipped
        - total_failures, caught_failures, missed_failures
        - fault_recall, test_savings_rate
    """
    total_tests = 0
    total_selected = 0
    total_skipped = 0
    total_failures = 0
    caught_failures = 0
    missed_failures = 0

    for rec in build_records:
        result = baseline.select(rec)
        selected_set = set(result["selected_tests"])

        for outcome in rec.get("test_outcomes", []):
            name = outcome.get("testName")
            if not name:
                continue
            total_tests += 1
            is_failure = outcome.get("verdict") == "1"

            if name in selected_set:
                total_selected += 1
                if is_failure:
                    total_failures += 1
                    caught_failures += 1
            else:
                total_skipped += 1
                if is_failure:
                    total_failures += 1
                    missed_failures += 1

    fault_recall = caught_failures / max(1, total_failures)
    test_savings = total_skipped / max(1, total_tests)

    metrics = {
        "baseline": baseline.name,
        "label": label,
        "total_builds": len(build_records),
        "total_tests": total_tests,
        "total_selected": total_selected,
        "total_skipped": total_skipped,
        "total_failures": total_failures,
        "caught_failures": caught_failures,
        "missed_failures": missed_failures,
        "fault_recall": round(fault_recall, 6),
        "test_savings_rate": round(test_savings, 6),
    }

    print(f"\n  [{baseline.name}] {label}")
    print(f"    Builds: {len(build_records)}")
    print(f"    Tests:  {total_tests:,}  Selected: {total_selected:,}  Skipped: {total_skipped:,}")
    print(f"    Failures: {total_failures:,}  Caught: {caught_failures:,}  Missed: {missed_failures:,}")
    print(f"    Fault Recall: {fault_recall:.4%}")
    print(f"    Test Savings: {test_savings:.4%}")

    return metrics


def load_slice(path):
    """Load a JSONL slice file into a list of records."""
    records = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            records.append(json.loads(line))
    return records


def run_baselines(project_key, slice_file=None, select_fraction=0.5, train_slice_file=None):
    """Run all baselines on a given project and evaluation slice."""
    proj = PROJECTS[project_key]
    project_folder = proj["folder"]

    eval_path = Path(slice_file) if slice_file else project_folder / "slice_3_eval.jsonl"
    if not eval_path.exists():
        print(f"ERROR: Evaluation slice not found at {eval_path}")
        print("Run split_dataset.py first to create chronological slices.")
        sys.exit(1)

    print(f"Loading evaluation slice from {eval_path.name}...")
    eval_records = load_slice(eval_path)
    print(f"  Loaded {len(eval_records)} build records")

    results = {}

    # Baseline A: Run All
    baseline_a = RunAllBaseline()
    results["RunAll"] = evaluate_baseline(baseline_a, eval_records, "Evaluation Slice")

    # Baseline B: File Path Heuristic
    baseline_b = FilePathHeuristicBaseline()
    results["FilePathHeuristic"] = evaluate_baseline(baseline_b, eval_records, "Evaluation Slice")

    # Baseline C: TCP-CI Historical ML Ranker
    train_path = Path(train_slice_file) if train_slice_file else project_folder / "slice_1_tune.jsonl"
    baseline_c = TCPCIHistoricalBaseline(select_fraction=select_fraction)
    if train_path.exists():
        print(f"\nTraining Baseline C on {train_path.name}...")
        train_records = load_slice(train_path)
        baseline_c.fit(train_records)
        results["TCPCIHistorical"] = evaluate_baseline(baseline_c, eval_records, "Evaluation Slice")
    else:
        print(f"\n  WARNING: Training slice {train_path} not found -- skipping Baseline C.")

    # Save results
    results_path = project_folder / "baseline_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nBaseline results saved to {results_path}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", choices=list(PROJECTS.keys()), help="Project key")
    parser.add_argument("--slice", help="Path to evaluation slice JSONL (default: slice_3_eval.jsonl)")
    parser.add_argument("--train-slice", help="Path to training slice JSONL for Baseline C (default: slice_1_tune.jsonl)")
    parser.add_argument("--select-fraction", type=float, default=0.5,
                        help="Fraction of tests Baseline C selects (default: 0.5)")
    args = parser.parse_args()

    run_baselines(args.project, args.slice, args.select_fraction, args.train_slice)
