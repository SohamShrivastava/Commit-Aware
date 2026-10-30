"""
Task 4: Unified Master Experiment Runner.

Single orchestrator that reproduces the entire pipeline for any given
project in one command. Sequentially executes:

    1. Dataset Slicing (split_dataset.py)
    2. Baseline Evaluation (baselines.py via metrics.py)
    3. Diff Embedding Precomputation (semantic_selector.py)
    4. Rho Selection Sweep (rho_selection.py)
    5. Conformal Calibration (calibration.py)
    6. Drift Monitoring (drift_monitor.py)
    7. Full Metrics Evaluation (metrics.py)
    8. Extended Cost Analysis (cost_analysis.py)
    9. LLM Batch Selection — dry run (llm_batch_selector.py)

Produces an aggregated summary_report.json with all key paper numbers.

Usage:
    python experiments/run_pipeline.py log4j2
    python experiments/run_pipeline.py shardingsphere
    python experiments/run_pipeline.py shardingsphere --skip-llm --limit 50
"""
import json
import argparse
import sys
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import PROJECTS


def run_step(step_name, func, **kwargs):
    """Run a pipeline step with timing and error handling.

    Returns:
        (result, elapsed_seconds, success_bool)
    """
    print(f"\n{'='*70}")
    print(f"  STEP: {step_name}")
    print(f"{'='*70}")
    start = time.time()
    try:
        result = func(**kwargs)
        elapsed = time.time() - start
        print(f"\n  [OK] {step_name} completed in {elapsed:.1f}s")
        return result, elapsed, True
    except Exception as e:
        elapsed = time.time() - start
        print(f"\n  [FAIL] {step_name} failed after {elapsed:.1f}s: {e}")
        import traceback
        traceback.print_exc()
        return None, elapsed, False


def run_pipeline(project_key, skip_slicing=False, skip_baselines=False,
                 skip_rho=False, skip_llm=False, limit=None,
                 alpha=0.05, rho=None):
    """Execute the full pipeline end-to-end.

    Args:
        project_key: Key into PROJECTS config.
        skip_slicing: Skip dataset slicing if slices already exist.
        skip_baselines: Skip baseline evaluation.
        skip_rho: Skip rho parameter sweep.
        skip_llm: Skip LLM batch selector (requires API).
        limit: Process only first N builds per step.
        alpha: Target error ceiling.
        rho: Override rho value (if None, uses sweep result).

    Returns:
        Aggregated summary report dict.
    """
    proj = PROJECTS[project_key]
    project_folder = proj["folder"]

    print(f"\n{'#'*70}")
    print(f"  MASTER PIPELINE: {project_key}")
    print(f"  Project folder: {project_folder}")
    print(f"  Started at: {datetime.now().isoformat()}")
    print(f"{'#'*70}")

    report = {
        "project": project_key,
        "pipeline_start": datetime.now().isoformat(),
        "steps": {},
    }

    total_start = time.time()

    # ──────────────────────────────────────────────────────────
    # Step 1: Dataset Slicing
    # ──────────────────────────────────────────────────────────
    if skip_slicing:
        print("\n  [SKIP] Dataset slicing (--skip-slicing)")
        report["steps"]["slicing"] = {"status": "skipped"}
    else:
        slice_path = project_folder / "slice_1_tune.jsonl"
        if slice_path.exists():
            print("\n  [SKIP] Slices already exist. Use --force-slicing to re-run.")
            report["steps"]["slicing"] = {"status": "skipped_exists"}
        else:
            from src.data_pipeline.split_dataset import run_split
            result, elapsed, ok = run_step(
                "Dataset Slicing",
                run_split,
                project_key=project_key,
            )
            report["steps"]["slicing"] = {
                "status": "ok" if ok else "failed",
                "elapsed_sec": elapsed,
            }
            if not ok:
                print("FATAL: Cannot proceed without dataset slices.")
                return report

    # ──────────────────────────────────────────────────────────
    # Step 2: Baseline Evaluation + Full Metrics
    # ──────────────────────────────────────────────────────────
    if skip_baselines:
        print("\n  [SKIP] Baseline evaluation (--skip-baselines)")
        report["steps"]["baselines"] = {"status": "skipped"}
    else:
        from src.evaluation.metrics import run_evaluation
        result, elapsed, ok = run_step(
            "Baseline + Semantic + Conformal Evaluation",
            run_evaluation,
            project_key=project_key,
        )
        report["steps"]["evaluation"] = {
            "status": "ok" if ok else "failed",
            "elapsed_sec": elapsed,
        }
        if ok and result:
            # Extract key numbers
            for method in result.get("methods", []):
                method_key = method["method"].lower().replace(" ", "_").replace("(", "").replace(")", "")
                report["steps"]["evaluation"][method_key] = {
                    "fault_recall": method.get("fault_recall"),
                    "test_reduction": method.get("test_reduction_rate"),
                    "net_savings_usd": method.get("cost_benefit", {}).get("net_savings_usd"),
                }

    # ──────────────────────────────────────────────────────────
    # Step 3: Rho Selection Sweep
    # ──────────────────────────────────────────────────────────
    selected_rho = rho or 0.995  # Default
    if skip_rho:
        print("\n  [SKIP] Rho selection sweep (--skip-rho)")
        report["steps"]["rho_sweep"] = {"status": "skipped"}
    else:
        from src.conformal.rho_selection import run_rho_sweep
        rho_kwargs = {"project_key": project_key, "alpha": alpha}
        if limit:
            rho_kwargs["limit"] = limit
        result, elapsed, ok = run_step(
            "Rho Parameter Sweep",
            run_rho_sweep,
            **rho_kwargs,
        )
        report["steps"]["rho_sweep"] = {
            "status": "ok" if ok else "failed",
            "elapsed_sec": elapsed,
        }
        if ok and result:
            selected_rho = result.get("selected_rho", 0.995)
            report["steps"]["rho_sweep"]["selected_rho"] = selected_rho

    # ──────────────────────────────────────────────────────────
    # Step 4: Conformal Calibration (with selected rho)
    # ──────────────────────────────────────────────────────────
    cal_path = project_folder / "calibration_results.json"
    if cal_path.exists() and skip_rho:
        print("\n  [SKIP] Calibration (using existing calibration_results.json)")
        with open(cal_path, encoding="utf-8") as f:
            cal_data = json.load(f)
        lambda_hat = cal_data.get("lambda_hat", 0.5)
        report["steps"]["calibration"] = {
            "status": "skipped_exists",
            "lambda_hat": lambda_hat,
        }
    else:
        from src.conformal.calibration import run_calibration
        cal_kwargs = {
            "project_key": project_key,
            "alpha": alpha,
            "rho": selected_rho,
        }
        if limit:
            cal_kwargs["limit"] = limit
        result, elapsed, ok = run_step(
            f"Conformal Calibration (rho={selected_rho})",
            run_calibration,
            **cal_kwargs,
        )
        report["steps"]["calibration"] = {
            "status": "ok" if ok else "failed",
            "elapsed_sec": elapsed,
        }
        if ok and result:
            lambda_hat = result.get("lambda_hat", 0.5)
            report["steps"]["calibration"]["lambda_hat"] = lambda_hat
            report["steps"]["calibration"]["rho_used"] = selected_rho

    # ──────────────────────────────────────────────────────────
    # Step 5: Drift Monitoring
    # ──────────────────────────────────────────────────────────
    from src.drift.drift_monitor import run_drift_monitor
    drift_kwargs = {"project_key": project_key}
    if limit:
        drift_kwargs["limit"] = limit
    result, elapsed, ok = run_step(
        "Drift Monitoring",
        run_drift_monitor,
        **drift_kwargs,
    )
    report["steps"]["drift_monitor"] = {
        "status": "ok" if ok else "failed",
        "elapsed_sec": elapsed,
    }
    if ok and result:
        summary = result.get("summary", {})
        report["steps"]["drift_monitor"]["drift_alarms"] = summary.get("total_drift_alarms")
        report["steps"]["drift_monitor"]["recalibrations"] = summary.get("total_recalibrations")
        report["steps"]["drift_monitor"]["final_lambda_hat"] = summary.get("final_lambda_hat")
        report["steps"]["drift_monitor"]["mean_miss_rate"] = summary.get("miss_rate_stats", {}).get("mean")

    # ──────────────────────────────────────────────────────────
    # Step 6: Extended Cost Analysis
    # ──────────────────────────────────────────────────────────
    from src.evaluation.cost_analysis import run_cost_analysis
    result, elapsed, ok = run_step(
        "Extended Cost Analysis",
        run_cost_analysis,
        project_key=project_key,
    )
    report["steps"]["cost_analysis"] = {
        "status": "ok" if ok else "failed",
        "elapsed_sec": elapsed,
    }

    # ──────────────────────────────────────────────────────────
    # Step 7: LLM Batch Selector (dry-run by default)
    # ──────────────────────────────────────────────────────────
    if skip_llm:
        print("\n  [SKIP] LLM Batch Selector (--skip-llm)")
        report["steps"]["llm_batch_selector"] = {"status": "skipped"}
    else:
        from src.online_selection.llm_batch_selector import run_llm_batch_selection
        llm_kwargs = {
            "project_key": project_key,
            "dry_run": True,  # Default to dry-run to avoid API costs
        }
        if limit:
            llm_kwargs["limit"] = limit
        result, elapsed, ok = run_step(
            "LLM Batch Selector (dry-run)",
            run_llm_batch_selection,
            **llm_kwargs,
        )
        report["steps"]["llm_batch_selector"] = {
            "status": "ok" if ok else "failed",
            "elapsed_sec": elapsed,
        }
        if ok and result:
            summary = result.get("summary", {})
            report["steps"]["llm_batch_selector"]["fault_recall"] = summary.get("fault_recall")
            report["steps"]["llm_batch_selector"]["test_reduction"] = summary.get("test_reduction_rate")

    # ──────────────────────────────────────────────────────────
    # Final Summary
    # ──────────────────────────────────────────────────────────
    total_elapsed = time.time() - total_start
    report["pipeline_end"] = datetime.now().isoformat()
    report["total_elapsed_sec"] = round(total_elapsed, 1)
    report["total_elapsed_min"] = round(total_elapsed / 60.0, 1)

    # Count successes
    step_statuses = {k: v.get("status", "unknown") for k, v in report["steps"].items()}
    report["step_summary"] = step_statuses

    print(f"\n{'#'*70}")
    print(f"  PIPELINE COMPLETE: {project_key}")
    print(f"  Total time: {total_elapsed/60:.1f} minutes")
    print(f"  Step statuses:")
    for step, status in step_statuses.items():
        icon = "[OK]" if status in ("ok", "skipped", "skipped_exists") else "[FAIL]"
        print(f"    {icon} {step}: {status}")
    print(f"{'#'*70}")

    # Save report
    report_path = project_folder / "summary_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nSummary report saved to {report_path}")

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project", choices=list(PROJECTS.keys()),
                        help="Project key (log4j2 or shardingsphere)")
    parser.add_argument("--skip-slicing", action="store_true",
                        help="Skip dataset slicing step")
    parser.add_argument("--skip-baselines", action="store_true",
                        help="Skip baseline evaluation")
    parser.add_argument("--skip-rho", action="store_true",
                        help="Skip rho parameter sweep")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Skip LLM batch selector")
    parser.add_argument("--limit", type=int,
                        help="Process only first N builds per step (for testing)")
    parser.add_argument("--alpha", type=float, default=0.05,
                        help="Target error ceiling (default: 0.05)")
    parser.add_argument("--rho", type=float,
                        help="Override rho value (skips sweep)")
    args = parser.parse_args()

    run_pipeline(
        args.project,
        skip_slicing=args.skip_slicing,
        skip_baselines=args.skip_baselines,
        skip_rho=args.skip_rho,
        skip_llm=args.skip_llm,
        limit=args.limit,
        alpha=args.alpha,
        rho=args.rho,
    )
