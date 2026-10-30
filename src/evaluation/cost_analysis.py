"""
Task 3: Extended Real-World Impact Metrics & Cost Analysis.

Expands the evaluation suite with industry-focused sustainability
and operational metrics for the ICSE 2027 paper:

    1. Break-Even PR Horizon (N_break-even):
       How many commits before offline indexing cost is recouped.

    2. Developer CI Feedback Latency Saved:
       Hours/minutes of pipeline waiting eliminated per sprint/month.

    3. Green Computing / CO2e Carbon Emissions Avoided:
       Estimated carbon footprint reduction using standard PUE and
       regional grid emission factors.

    4. Aggregated ROI Summary:
       Single table with all financial, time, and environmental impact.

Usage:
    python -m src.evaluation.cost_analysis log4j2
    python -m src.evaluation.cost_analysis shardingsphere
    python -m src.evaluation.cost_analysis shardingsphere --annual-builds 5000
"""
import json
import argparse
import sys
from pathlib import Path

# pyrefly: ignore [missing-import]
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.config import PROJECTS


# ── Industry Cost Constants ──
# Cloud VM costs (GCP e2-standard-2 typical)
DEFAULT_VM_COST_PER_HOUR_USD = 0.10      # Conservative estimate
DEFAULT_PREMIUM_VM_COST_PER_HOUR_USD = 0.50  # For large enterprise suites

# Offline indexing cost (from verified Google billing)
OFFLINE_INDEXING_COST_INR = 743.89       # Total for both projects
INR_TO_USD = 0.012                       # Approximate conversion
OFFLINE_INDEXING_COST_USD = round(OFFLINE_INDEXING_COST_INR * INR_TO_USD, 2)

# Per-project offline costs (from agent.md billing table)
OFFLINE_COSTS_INR = {
    "log4j2": 250.16 + 2.43,       # Summaries + Embeddings
    "shardingsphere": 487.12 + 4.18,
}

# Energy and carbon constants
# Source: Google Cloud Sustainability Report + EPA eGRID
DATACENTER_PUE = 1.10                    # Google Cloud average PUE
AVG_SERVER_POWER_KW = 0.35              # Average server power draw (kW)
CARBON_INTENSITY_KG_CO2_PER_KWH = 0.42  # US average grid (kg CO2e/kWh)
# European grid is lower (~0.25), Asian grid higher (~0.55)

# Developer productivity constants
AVG_DEVELOPER_HOURLY_RATE_USD = 75.0    # Mid-level SWE cost
AVG_CI_QUEUE_OVERHEAD_MIN = 2.0         # Queue + setup time per CI run
DEFAULT_AVG_TEST_DURATION_SEC = 5.0     # Fallback per-test time

# API costs
EMBED_COST_PER_1K_TOKENS_USD = 0.0001
LLM_COST_PER_1K_TOKENS_USD = 0.0005
AVG_DIFF_TOKENS = 2000


def compute_break_even_horizon(offline_cost_usd, net_savings_per_build_usd):
    """Compute the break-even PR/commit horizon.

    N_break-even = Offline Indexing Cost / Net Savings per Build

    Args:
        offline_cost_usd: One-time offline indexing cost.
        net_savings_per_build_usd: Average net savings per CI build.

    Returns:
        dict with break-even builds, estimated days, and months.
    """
    if net_savings_per_build_usd <= 0:
        return {
            "break_even_builds": float("inf"),
            "break_even_days": float("inf"),
            "break_even_months": float("inf"),
            "note": "Net savings per build is <= 0; system does not break even.",
        }

    n_break_even = offline_cost_usd / net_savings_per_build_usd

    # Assume typical CI cadence: ~10 builds/day for active open-source projects
    builds_per_day = 10.0
    days = n_break_even / builds_per_day
    months = days / 30.0

    return {
        "break_even_builds": round(n_break_even, 1),
        "break_even_days": round(days, 1),
        "break_even_months": round(months, 2),
        "offline_cost_usd": round(offline_cost_usd, 2),
        "net_savings_per_build_usd": round(net_savings_per_build_usd, 6),
        "assumed_builds_per_day": builds_per_day,
    }


def compute_latency_saved(total_builds, total_tests_skipped,
                          avg_test_duration_sec=DEFAULT_AVG_TEST_DURATION_SEC,
                          ci_queue_overhead_min=AVG_CI_QUEUE_OVERHEAD_MIN):
    """Compute developer CI feedback latency savings.

    Metrics:
        - Total test execution time saved (hours)
        - Average time saved per build (minutes)
        - Estimated developer waiting time saved (hours)
        - Sprint savings (assuming 10-day sprints, ~20 builds/day)

    Args:
        total_builds: Number of builds in the evaluation period.
        total_tests_skipped: Total tests that were skipped.
        avg_test_duration_sec: Average execution time per test.
        ci_queue_overhead_min: Fixed queue/setup overhead per CI run.

    Returns:
        dict with latency metrics.
    """
    # Raw test execution time saved
    seconds_saved = total_tests_skipped * avg_test_duration_sec
    hours_saved = seconds_saved / 3600.0
    minutes_per_build = (seconds_saved / max(1, total_builds)) / 60.0

    # Developer waiting time = test time + proportional queue savings
    # If suite runs 50% faster, queue contention drops proportionally
    dev_minutes_saved_per_build = minutes_per_build * 1.2  # 20% multiplier for reduced queue contention

    # Sprint projections (10-day sprint, ~20 builds/day)
    builds_per_sprint = 200
    sprint_hours_saved = (dev_minutes_saved_per_build * builds_per_sprint) / 60.0

    # Monthly projections (~22 working days, ~20 builds/day)
    builds_per_month = 440
    monthly_hours_saved = (dev_minutes_saved_per_build * builds_per_month) / 60.0

    # Annualized
    annual_hours_saved = monthly_hours_saved * 12

    return {
        "total_test_hours_saved": round(hours_saved, 2),
        "avg_minutes_saved_per_build": round(minutes_per_build, 2),
        "dev_minutes_saved_per_build": round(dev_minutes_saved_per_build, 2),
        "sprint_dev_hours_saved": round(sprint_hours_saved, 2),
        "monthly_dev_hours_saved": round(monthly_hours_saved, 2),
        "annual_dev_hours_saved": round(annual_hours_saved, 1),
        "annual_developer_cost_saved_usd": round(
            annual_hours_saved * AVG_DEVELOPER_HOURLY_RATE_USD, 2
        ),
    }


def compute_carbon_savings(total_tests_skipped,
                           avg_test_duration_sec=DEFAULT_AVG_TEST_DURATION_SEC,
                           pue=DATACENTER_PUE,
                           server_power_kw=AVG_SERVER_POWER_KW,
                           carbon_intensity=CARBON_INTENSITY_KG_CO2_PER_KWH):
    """Compute estimated CO2e emissions avoided.

    CO2e = (test_hours_saved * server_power_kW * PUE) * carbon_intensity

    Args:
        total_tests_skipped: Number of test executions avoided.
        avg_test_duration_sec: Average per-test runtime.
        pue: Power Usage Effectiveness of the datacenter.
        server_power_kw: Average server power draw in kW.
        carbon_intensity: Regional grid carbon intensity (kg CO2e / kWh).

    Returns:
        dict with carbon metrics.
    """
    hours_saved = (total_tests_skipped * avg_test_duration_sec) / 3600.0

    # Energy saved (kWh) = hours * power_draw(kW) * PUE
    energy_kwh = hours_saved * server_power_kw * pue

    # Carbon emissions avoided
    co2e_kg = energy_kwh * carbon_intensity
    co2e_metric_tons = co2e_kg / 1000.0

    # Annualized projections (assuming evaluation period covers ~40% of year)
    # Scale factor based on builds observed vs typical annual volume
    annual_co2e_kg = co2e_kg * 2.5  # Conservative annual scaling

    # Equivalencies for paper narrative
    # 1 tree absorbs ~22 kg CO2/year (US Forest Service)
    # 1 mile driven = ~0.411 kg CO2 (EPA)
    trees_equivalent = annual_co2e_kg / 22.0
    miles_equivalent = annual_co2e_kg / 0.411

    return {
        "test_hours_saved": round(hours_saved, 4),
        "energy_saved_kwh": round(energy_kwh, 4),
        "co2e_avoided_kg": round(co2e_kg, 4),
        "co2e_avoided_metric_tons": round(co2e_metric_tons, 6),
        "annual_co2e_avoided_kg": round(annual_co2e_kg, 2),
        "annual_trees_equivalent": round(trees_equivalent, 1),
        "annual_driving_miles_equivalent": round(miles_equivalent, 1),
        "assumptions": {
            "pue": pue,
            "server_power_kw": server_power_kw,
            "carbon_intensity_kg_co2_per_kwh": carbon_intensity,
        },
    }


def compute_roi_summary(offline_cost_usd, eval_results, annual_builds=5000):
    """Compute comprehensive ROI summary for a method.

    Args:
        offline_cost_usd: One-time offline indexing cost.
        eval_results: Evaluation results dict for one method.
        annual_builds: Projected annual CI build volume.

    Returns:
        dict with complete ROI analysis.
    """
    cost_benefit = eval_results.get("cost_benefit", {})
    total_builds = eval_results.get("total_builds", 1)
    net_savings_total = cost_benefit.get("net_savings_usd", 0.0)
    tests_skipped = cost_benefit.get("tests_skipped", 0)

    net_savings_per_build = net_savings_total / max(1, total_builds)

    # Annual projections
    annual_savings = net_savings_per_build * annual_builds
    annual_api_cost = (cost_benefit.get("api_cost_usd", 0.0) / max(1, total_builds)) * annual_builds
    annual_compute_saved = (cost_benefit.get("compute_saved_usd", 0.0) / max(1, total_builds)) * annual_builds

    # Multi-year cumulative (accounting for periodic re-indexing every 6 months)
    reindex_annual_cost = offline_cost_usd * 2  # Re-index twice/year
    year_1_net = annual_savings - offline_cost_usd  # First index + one re-index
    year_2_net = annual_savings - reindex_annual_cost
    year_3_net = annual_savings - reindex_annual_cost
    cumulative_3_year = year_1_net + year_2_net + year_3_net

    return {
        "offline_cost_usd": round(offline_cost_usd, 2),
        "net_savings_per_build_usd": round(net_savings_per_build, 6),
        "annual_builds": annual_builds,
        "annual_compute_saved_usd": round(annual_compute_saved, 2),
        "annual_api_cost_usd": round(annual_api_cost, 2),
        "annual_net_savings_usd": round(annual_savings, 2),
        "year_1_net_usd": round(year_1_net, 2),
        "year_2_net_usd": round(year_2_net, 2),
        "year_3_net_usd": round(year_3_net, 2),
        "cumulative_3_year_usd": round(cumulative_3_year, 2),
        "roi_percentage": round((cumulative_3_year / max(0.01, offline_cost_usd)) * 100, 1),
    }


def run_cost_analysis(project_key, vm_cost=DEFAULT_VM_COST_PER_HOUR_USD,
                      annual_builds=5000):
    """Run comprehensive cost analysis for a project.

    Loads evaluation_results.json and computes extended metrics for
    each evaluated method.

    Args:
        project_key: Project key.
        vm_cost: VM cost per hour (USD).
        annual_builds: Projected annual CI build count.

    Returns:
        dict with comprehensive cost analysis.
    """
    proj = PROJECTS[project_key]
    project_folder = proj["folder"]

    # Load evaluation results
    eval_path = project_folder / "evaluation_results.json"
    if not eval_path.exists():
        print(f"ERROR: evaluation_results.json not found in {project_folder}")
        print("Run metrics.py first to generate evaluation results.")
        sys.exit(1)

    with open(eval_path, encoding="utf-8") as f:
        eval_data = json.load(f)

    # Project-specific offline cost
    offline_cost_inr = OFFLINE_COSTS_INR.get(project_key, OFFLINE_INDEXING_COST_INR / 2)
    offline_cost_usd = round(offline_cost_inr * INR_TO_USD, 2)

    print(f"\n{'='*70}")
    print(f"Extended Cost Analysis: {project_key}")
    print(f"{'='*70}")
    print(f"  Offline indexing cost: INR {offline_cost_inr:.2f} (~${offline_cost_usd})")
    print(f"  VM cost: ${vm_cost}/hr")
    print(f"  Annual build projection: {annual_builds} builds/year")

    methods_analysis = []

    for method_result in eval_data.get("methods", []):
        method_name = method_result.get("method", "Unknown")
        print(f"\n--- {method_name} ---")

        cost_benefit = method_result.get("cost_benefit", {})
        total_builds = method_result.get("total_builds", 1)
        total_tests = method_result.get("total_tests", 0)
        tests_skipped = cost_benefit.get("tests_skipped", 0)
        net_savings = cost_benefit.get("net_savings_usd", 0.0)

        net_per_build = net_savings / max(1, total_builds)

        # 1. Break-even horizon
        break_even = compute_break_even_horizon(offline_cost_usd, net_per_build)
        print(f"  Break-even: {break_even['break_even_builds']:.0f} builds "
              f"(~{break_even.get('break_even_days', 'inf'):.0f} days)")

        # 2. Latency saved
        latency = compute_latency_saved(total_builds, tests_skipped)
        print(f"  Latency saved: {latency['avg_minutes_saved_per_build']:.1f} min/build, "
              f"{latency['annual_dev_hours_saved']:.0f} dev-hrs/year")

        # 3. Carbon savings
        carbon = compute_carbon_savings(tests_skipped)
        print(f"  Carbon avoided: {carbon['co2e_avoided_kg']:.3f} kg CO2e "
              f"(eval period), ~{carbon['annual_co2e_avoided_kg']:.1f} kg/year")

        # 4. ROI
        roi = compute_roi_summary(offline_cost_usd, method_result, annual_builds)
        print(f"  3-year cumulative ROI: ${roi['cumulative_3_year_usd']:.2f} "
              f"({roi['roi_percentage']:.1f}%)")

        methods_analysis.append({
            "method": method_name,
            "fault_recall": method_result.get("fault_recall", 0.0),
            "test_reduction_rate": method_result.get("test_reduction_rate", 0.0),
            "break_even": break_even,
            "latency": latency,
            "carbon": carbon,
            "roi": roi,
        })

    # ── Print summary table ──
    print(f"\n{'='*90}")
    print(f"{'Method':<25} {'Recall':>8} {'Skip%':>7} {'BrkEvn':>8} "
          f"{'Min/Bld':>8} {'CO2/yr':>8} {'3yr ROI':>10}")
    print(f"{'-'*90}")

    for m in methods_analysis:
        recall = f"{m['fault_recall']:.0%}"
        skip = f"{m['test_reduction_rate']:.1%}"
        brk = (f"{m['break_even']['break_even_builds']:.0f}"
               if m['break_even']['break_even_builds'] != float('inf') else "N/A")
        latmin = f"{m['latency']['avg_minutes_saved_per_build']:.1f}"
        co2 = f"{m['carbon']['annual_co2e_avoided_kg']:.1f}kg"
        roi = f"${m['roi']['cumulative_3_year_usd']:.0f}"
        print(f"{m['method']:<25} {recall:>8} {skip:>7} {brk:>8} "
              f"{latmin:>8} {co2:>8} {roi:>10}")

    print(f"{'='*90}")

    # ── Save results ──
    output = {
        "project": project_key,
        "offline_indexing_cost_inr": offline_cost_inr,
        "offline_indexing_cost_usd": offline_cost_usd,
        "vm_cost_per_hour_usd": vm_cost,
        "annual_builds_projected": annual_builds,
        "assumptions": {
            "datacenter_pue": DATACENTER_PUE,
            "server_power_kw": AVG_SERVER_POWER_KW,
            "carbon_intensity_kg_co2_per_kwh": CARBON_INTENSITY_KG_CO2_PER_KWH,
            "avg_developer_hourly_rate_usd": AVG_DEVELOPER_HOURLY_RATE_USD,
            "avg_test_duration_sec": DEFAULT_AVG_TEST_DURATION_SEC,
        },
        "methods": methods_analysis,
    }

    results_path = project_folder / "cost_analysis_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False,
                  default=lambda x: str(x) if isinstance(x, float) and (x == float('inf') or x != x) else x)
    print(f"\nCost analysis saved to {results_path}")

    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project", choices=list(PROJECTS.keys()),
                        help="Project key")
    parser.add_argument("--vm-cost", type=float, default=DEFAULT_VM_COST_PER_HOUR_USD,
                        help=f"VM cost per hour in USD (default: {DEFAULT_VM_COST_PER_HOUR_USD})")
    parser.add_argument("--annual-builds", type=int, default=5000,
                        help="Projected annual CI build volume (default: 5000)")
    args = parser.parse_args()

    run_cost_analysis(args.project, args.vm_cost, args.annual_builds)
