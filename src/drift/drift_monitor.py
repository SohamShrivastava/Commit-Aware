"""
Step 5a: Concept Drift Detection & Threshold Adaptation.

Monitors the rolling empirical fault miss-rate over the latest W builds
during the evaluation phase. If the achieved risk breaches α + ε due to
test taxonomy or architectural shifts, triggers automatic recalibration.

Key mechanism:
    - Maintain a sliding window of the most recent W builds.
    - Compute the running fault miss-rate within this window.
    - If miss-rate > α + ε for ε_count consecutive windows, trigger
      recalibration by widening λ̂ (decreasing the threshold).

Usage:
    python -m src.drift.drift_monitor log4j2
    python -m src.drift.drift_monitor shardingsphere --window-size 30 --epsilon 0.02
"""
import json
import argparse
import sys
from pathlib import Path
from collections import deque

# pyrefly: ignore [missing-import]
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.config import PROJECTS


# ── Defaults ──
DEFAULT_ALPHA = 0.05         # Target error ceiling
DEFAULT_WINDOW_SIZE = 50     # Rolling window of W builds
DEFAULT_EPSILON = 0.02       # Drift alarm margin above alpha
DEFAULT_ALARM_COUNT = 3      # Consecutive breach windows before recalibration
DEFAULT_RECAL_SHRINK = 0.05  # How much to lower lambda_hat on each recalibration


class DriftMonitor:
    """Monitors rolling fault miss-rate and triggers threshold adaptation
    when concept drift is detected.

    Attributes:
        alpha: Target error ceiling from conformal calibration.
        epsilon: Margin above α that triggers drift alarm.
        window_size: Number of recent builds in the rolling window.
        lambda_hat: Current selection threshold (modified on recalibration).
        history: Full history of per-build miss-rates and drift events.
    """

    def __init__(self, lambda_hat, alpha=0.05, epsilon=DEFAULT_EPSILON,
                 window_size=DEFAULT_WINDOW_SIZE,
                 alarm_count=DEFAULT_ALARM_COUNT,
                 recal_shrink=DEFAULT_RECAL_SHRINK):
        """
        Args:
            lambda_hat:   Initial calibrated threshold from Step 4.
            alpha:        Target error ceiling.
            epsilon:      Drift alarm margin.
            window_size:  Number of builds in the rolling window.
            alarm_count:  Consecutive breaches before recalibration.
            recal_shrink: Amount to lower λ̂ on each recalibration.
        """
        self.alpha = alpha
        self.epsilon = epsilon
        self.window_size = window_size
        self.alarm_count = alarm_count
        self.recal_shrink = recal_shrink

        self.lambda_hat = lambda_hat
        self.lambda_history = [lambda_hat]

        # Rolling window of per-build miss-rates
        self._window = deque(maxlen=window_size)
        self._consecutive_breaches = 0
        self._total_recalibrations = 0

        # Full history for analysis
        self.history = []

    def observe(self, build_id, test_results):
        """Process one build's test results and check for drift.

        Args:
            build_id: Identifier for the build.
            test_results: List of dicts, each containing:
                {
                    "test_name": str,
                    "similarity": float,
                    "is_failure": bool,
                }

        Returns:
            dict with:
                - build_id
                - miss_rate: this build's fault miss-rate
                - rolling_miss_rate: average over the window
                - drift_detected: bool
                - recalibrated: bool
                - lambda_hat: current threshold (possibly updated)
        """
        # Compute this build's fault miss-rate
        failures = [t for t in test_results if t["is_failure"]]
        if not failures:
            miss_rate = 0.0
        else:
            missed = sum(1 for t in failures if t["similarity"] < self.lambda_hat)
            miss_rate = missed / len(failures)

        self._window.append(miss_rate)

        # Compute rolling average miss-rate
        rolling_miss_rate = float(np.mean(list(self._window)))

        # Check for drift
        breach = rolling_miss_rate > (self.alpha + self.epsilon)
        recalibrated = False

        if breach:
            self._consecutive_breaches += 1
        else:
            self._consecutive_breaches = 0

        # Trigger recalibration if breaches persist
        if self._consecutive_breaches >= self.alarm_count:
            old_lambda = self.lambda_hat
            self.lambda_hat = max(0.0, self.lambda_hat - self.recal_shrink)
            self._total_recalibrations += 1
            self._consecutive_breaches = 0
            recalibrated = True
            self.lambda_history.append(self.lambda_hat)
            print(f"  [!] DRIFT DETECTED at build {build_id}: "
                  f"rolling miss-rate={rolling_miss_rate:.4f} > "
                  f"alpha+eps={self.alpha + self.epsilon:.4f}")
            print(f"    Recalibrated lambda_hat: {old_lambda:.6f} -> {self.lambda_hat:.6f} "
                  f"(recalibration #{self._total_recalibrations})")

        entry = {
            "build_id": build_id,
            "miss_rate": round(miss_rate, 6),
            "rolling_miss_rate": round(rolling_miss_rate, 6),
            "drift_detected": breach,
            "recalibrated": recalibrated,
            "lambda_hat": self.lambda_hat,
            "window_size": len(self._window),
            "consecutive_breaches": self._consecutive_breaches,
        }
        self.history.append(entry)
        return entry

    def get_summary(self):
        """Return a summary of drift monitoring results."""
        if not self.history:
            return {"error": "No builds observed yet."}

        miss_rates = [h["miss_rate"] for h in self.history]
        rolling_rates = [h["rolling_miss_rate"] for h in self.history]
        drift_events = sum(1 for h in self.history if h["drift_detected"])
        recal_events = sum(1 for h in self.history if h["recalibrated"])

        return {
            "total_builds_monitored": len(self.history),
            "total_drift_alarms": drift_events,
            "total_recalibrations": recal_events,
            "final_lambda_hat": self.lambda_hat,
            "lambda_history": self.lambda_history,
            "miss_rate_stats": {
                "mean": round(float(np.mean(miss_rates)), 6),
                "median": round(float(np.median(miss_rates)), 6),
                "max": round(float(np.max(miss_rates)), 6),
                "std": round(float(np.std(miss_rates)), 6),
            },
            "rolling_miss_rate_stats": {
                "mean": round(float(np.mean(rolling_rates)), 6),
                "median": round(float(np.median(rolling_rates)), 6),
                "max": round(float(np.max(rolling_rates)), 6),
            },
        }


def run_drift_monitor(project_key, lambda_hat=None, alpha=0.05, epsilon=DEFAULT_EPSILON,
                      window_size=DEFAULT_WINDOW_SIZE, eval_slice=None, limit=None):
    """Run the drift monitor over the evaluation slice.

    This requires precomputed similarity scores from the semantic selector.
    If calibration_results.json exists, uses its λ̂. Otherwise, uses a default.
    """
    proj = PROJECTS[project_key]
    project_folder = proj["folder"]

    # Load λ̂ from calibration results if not provided
    if lambda_hat is None:
        cal_path = project_folder / "calibration_results.json"
        if cal_path.exists():
            with open(cal_path, encoding="utf-8") as f:
                cal = json.loads(f.read())
            lambda_hat = cal.get("lambda_hat", 0.4)
            alpha = cal.get("alpha", alpha)
            print(f"Loaded lambda_hat={lambda_hat:.6f}, alpha={alpha} from calibration_results.json")
        else:
            lambda_hat = 0.4
            print(f"No calibration results found. Using default lambda_hat={lambda_hat}")

    eval_path = Path(eval_slice) if eval_slice else project_folder / "slice_3_eval.jsonl"
    if not eval_path.exists():
        print(f"ERROR: Evaluation slice not found: {eval_path}")
        sys.exit(1)

    # Load semantic selector results if available (avoids re-calling API)
    semantic_results_path = project_folder / "semantic_selector_results.json"
    precomputed_scores = {}

    if semantic_results_path.exists():
        print(f"Loading precomputed semantic scores from {semantic_results_path.name}...")
        with open(semantic_results_path, encoding="utf-8") as f:
            sem_results = json.loads(f.read())
        for pb in sem_results.get("per_build", []):
            precomputed_scores[str(pb["build_id"])] = pb
        print(f"  Loaded scores for {len(precomputed_scores)} builds")

    # Load evaluation slice
    print(f"Loading evaluation slice from {eval_path.name}...")
    records = []
    with open(eval_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            records.append(json.loads(line))
    if limit:
        records = records[:limit]
    print(f"  Loaded {len(records)} evaluation builds")

    # Initialize drift monitor
    monitor = DriftMonitor(
        lambda_hat=lambda_hat,
        alpha=alpha,
        epsilon=epsilon,
        window_size=window_size,
    )

    # If we don't have precomputed scores, we need the selector
    need_selector = any(str(r["build_id"]) not in precomputed_scores for r in records)
    selector = None
    if need_selector:
        try:
            from src.online_selection.semantic_selector import SemanticSelector
            selector = SemanticSelector(project_key, use_api=True)
        except Exception as e:
            print(f"WARNING: Cannot initialize selector: {e}")
            print("Will use default similarity scores for builds without precomputed scores.")

    print(f"\nRunning drift monitor (W={window_size}, eps={epsilon})...")
    for i, rec in enumerate(records):
        build_id = str(rec["build_id"])

        # Get test-level similarity scores
        test_results = []
        if build_id in precomputed_scores:
            # Use precomputed — but we need per-test scores, so match back
            # For now, use a placeholder approach: check scores dict
            pass

        # Build test_results from the build record + selector
        if selector:
            key = str(rec.get("commit_sha") or rec.get("build_id"))
            diff_emb = selector.embed_diff(rec.get("diff_text", ""), cache_key=key)
            similarities = selector.score_tests(diff_emb)
        else:
            similarities = None

        for outcome in rec.get("test_outcomes", []):
            name = outcome.get("testName")
            if not name:
                continue
            if selector and similarities is not None:
                idx = selector.path_to_idx.get(name)
                sim = float(similarities[idx]) if idx is not None and idx < len(similarities) else 0.5
            else:
                # Without selector, use a moderate default
                sim = 0.5
            test_results.append({
                "test_name": name,
                "similarity": sim,
                "is_failure": str(outcome.get("verdict")) in ("1", "2", "3"),
            })

        result = monitor.observe(build_id, test_results)

        if (i + 1) % 50 == 0:
            print(f"  Processed {i+1}/{len(records)} builds "
                  f"(rolling miss-rate: {result['rolling_miss_rate']:.4f}, "
                  f"lambda_hat={result['lambda_hat']:.6f})")

    # Summary
    summary = monitor.get_summary()
    print(f"\n{'=' * 60}")
    print(f"Drift Monitor Summary ({project_key})")
    print(f"  Builds monitored: {summary['total_builds_monitored']}")
    print(f"  Drift alarms: {summary['total_drift_alarms']}")
    print(f"  Recalibrations: {summary['total_recalibrations']}")
    print(f"  Final lambda_hat: {summary['final_lambda_hat']:.6f}")
    print(f"  Miss-rate: mean={summary['miss_rate_stats']['mean']:.4f}, "
          f"max={summary['miss_rate_stats']['max']:.4f}")
    print(f"{'=' * 60}")

    # Save results
    output = {
        "project": project_key,
        "config": {
            "initial_lambda_hat": lambda_hat,
            "alpha": alpha,
            "epsilon": epsilon,
            "window_size": window_size,
        },
        "summary": summary,
        "history": monitor.history,
    }
    results_path = project_folder / "drift_monitor_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Results saved to {results_path}")

    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", choices=list(PROJECTS.keys()), help="Project key")
    parser.add_argument("--lambda-hat", type=float, help="Override calibrated lambda_hat")
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA,
                        help=f"Target error ceiling (default: {DEFAULT_ALPHA})")
    parser.add_argument("--epsilon", type=float, default=DEFAULT_EPSILON,
                        help=f"Drift margin above alpha (default: {DEFAULT_EPSILON})")
    parser.add_argument("--window-size", type=int, default=DEFAULT_WINDOW_SIZE,
                        help=f"Rolling window size (default: {DEFAULT_WINDOW_SIZE})")
    parser.add_argument("--eval-slice", help="Evaluation slice JSONL path")
    parser.add_argument("--limit", type=int, help="Process only first N builds")
    args = parser.parse_args()

    run_drift_monitor(args.project, args.lambda_hat, args.alpha, args.epsilon,
                      args.window_size, args.eval_slice, args.limit)
