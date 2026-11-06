"""
Task 5: Publication-Ready Visualizations & LaTeX Tables.

Generates figures and formatted tables for the ICSE 2027 paper:

    Figure 1: Conformal Risk Curve
        Empirical risk R_w(lambda) vs threshold lambda with alpha=0.05 line.

    Figure 2: Concept Drift Timeline
        Build timeline showing rolling miss-rate, alarm windows,
        and automatic lambda_hat recalibrations.

    Figure 3: Cost-Recall Pareto Frontier
        Fault recall vs test reduction across all methods.

    Figure 4: Rho Sensitivity Analysis
        lambda_hat variance vs rho with effective sample size overlay.

    Table 1: Offline Indexing Cost Breakdown (LaTeX)
    Table 2: Comprehensive Multi-Dimensional Benchmark (LaTeX)
    Table 3: Extended Impact Metrics (LaTeX)

Usage:
    python -m src.visualization.paper_figures shardingsphere
    python -m src.visualization.paper_figures log4j2 --dpi 300
"""
import json
import argparse
import sys
from pathlib import Path

# pyrefly: ignore [missing-import]
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.config import PROJECTS

# Try to import matplotlib — if not available, generate tables only
try:
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    print("WARNING: matplotlib not installed. Will generate LaTeX tables only.")


# ── Publication styling ──
PAPER_COLORS = {
    "run_all": "#636363",       # Gray
    "path_heuristic": "#e6550d", # Orange
    "tcp_ci": "#3182bd",        # Blue
    "semantic": "#31a354",      # Green
    "conformal": "#756bb1",     # Purple
    "llm_hybrid": "#e7298a",    # Pink
}

METHOD_LABELS = {
    "Run All (Baseline A)": ("Run All", PAPER_COLORS["run_all"]),
    "Path Heuristic (B)": ("PathHeuristic", PAPER_COLORS["path_heuristic"]),
    "TCP-CI ML (C, top-50%)": ("TCP-CI ML", PAPER_COLORS["tcp_ci"]),
    "Semantic Selector": ("SemanticSel", PAPER_COLORS["semantic"]),
}


def setup_paper_style():
    """Configure matplotlib for publication-quality output."""
    if not HAS_MATPLOTLIB:
        return

    plt.rcParams.update({
        'font.family': 'serif',
        'font.serif': ['Times New Roman', 'DejaVu Serif'],
        'font.size': 10,
        'axes.labelsize': 11,
        'axes.titlesize': 12,
        'xtick.labelsize': 9,
        'ytick.labelsize': 9,
        'legend.fontsize': 9,
        'figure.figsize': (6, 4),
        'figure.dpi': 150,
        'savefig.dpi': 300,
        'savefig.bbox': 'tight',
        'axes.grid': True,
        'grid.alpha': 0.3,
        'grid.linestyle': '--',
    })


def figure_risk_curve(project_key, output_dir, dpi=300):
    """Figure 1: Conformal Risk Curve.

    Plots empirical weighted risk R_w(lambda) vs threshold lambda,
    showing the alpha=0.05 ceiling and the selected lambda_hat.
    """
    if not HAS_MATPLOTLIB:
        print("  [SKIP] Risk curve figure (no matplotlib)")
        return

    project_folder = PROJECTS[project_key]["folder"]
    cal_path = project_folder / "calibration_results.json"
    if not cal_path.exists():
        print(f"  [SKIP] No calibration_results.json for {project_key}")
        return

    with open(cal_path, encoding="utf-8") as f:
        cal_data = json.load(f)

    risk_curve = cal_data.get("risk_curve", [])
    lambda_hat = cal_data.get("lambda_hat", 0.5)
    alpha = cal_data.get("alpha", 0.05)

    if not risk_curve:
        print(f"  [SKIP] Empty risk curve for {project_key}")
        return

    lambdas = [r[0] for r in risk_curve]
    risks = [r[1] for r in risk_curve]

    fig, ax = plt.subplots(figsize=(6, 4))

    # Risk curve
    ax.plot(lambdas, risks, color=PAPER_COLORS["conformal"], linewidth=2,
            label=r'$\hat{R}_w(\lambda)$')

    # Alpha ceiling
    ax.axhline(y=alpha, color='red', linestyle='--', linewidth=1.5,
               label=rf'$\alpha = {alpha}$', alpha=0.8)

    # Lambda_hat marker
    ax.axvline(x=lambda_hat, color='green', linestyle=':', linewidth=1.5,
               label=rf'$\hat{{\lambda}} = {lambda_hat:.4f}$', alpha=0.8)

    # Mark the intersection
    ax.plot(lambda_hat, alpha, 'ko', markersize=8, zorder=5)

    ax.set_xlabel(r'Threshold $\lambda$ (cosine similarity)')
    ax.set_ylabel(r'Weighted Empirical Risk $\hat{R}_w(\lambda)$')
    ax.set_title(f'Conformal Risk Curve ({project_key})')
    ax.legend(loc='upper left', frameon=True, fancybox=True, shadow=True)
    ax.set_ylim(-0.01, max(risks) * 1.1)

    fig_path = output_dir / f"fig1_risk_curve_{project_key}.png"
    fig.savefig(str(fig_path), dpi=dpi)
    plt.close(fig)
    print(f"  Saved: {fig_path.name}")


def figure_drift_timeline(project_key, output_dir, dpi=300):
    """Figure 2: Concept Drift Timeline.

    Shows rolling miss-rate over the evaluation timeline with drift
    alarm windows and automatic recalibrations highlighted.
    """
    if not HAS_MATPLOTLIB:
        print("  [SKIP] Drift timeline figure (no matplotlib)")
        return

    project_folder = PROJECTS[project_key]["folder"]
    drift_path = project_folder / "drift_monitor_results.json"
    if not drift_path.exists():
        print(f"  [SKIP] No drift_monitor_results.json for {project_key}")
        return

    with open(drift_path, encoding="utf-8") as f:
        drift_data = json.load(f)

    history = drift_data.get("history", [])
    config = drift_data.get("config", {})
    alpha = config.get("alpha", 0.05)
    epsilon = config.get("epsilon", 0.02)

    if not history:
        print(f"  [SKIP] Empty drift history for {project_key}")
        return

    builds = list(range(len(history)))
    rolling_rates = [h["rolling_miss_rate"] for h in history]
    miss_rates = [h["miss_rate"] for h in history]
    lambda_vals = [h["lambda_hat"] for h in history]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), height_ratios=[3, 1],
                                    sharex=True)

    # Top panel: Miss rates
    ax1.fill_between(builds, miss_rates, alpha=0.15, color='gray',
                     label='Per-build miss-rate')
    ax1.plot(builds, rolling_rates, color=PAPER_COLORS["conformal"],
             linewidth=2, label='Rolling miss-rate')
    ax1.axhline(y=alpha, color='red', linestyle='--', linewidth=1,
                label=rf'$\alpha = {alpha}$')
    ax1.axhline(y=alpha + epsilon, color='orange', linestyle=':',
                linewidth=1, label=rf'$\alpha + \epsilon = {alpha+epsilon}$')

    # Highlight drift alarm windows
    for i, h in enumerate(history):
        if h.get("recalibrated"):
            ax1.axvline(x=i, color='red', alpha=0.4, linewidth=2)
        elif h.get("drift_detected"):
            ax1.axvspan(i-0.5, i+0.5, alpha=0.1, color='orange')

    ax1.set_ylabel('Fault Miss-Rate')
    ax1.set_title(f'Concept Drift Timeline ({project_key})')
    ax1.legend(loc='upper right', fontsize=8)
    ax1.set_ylim(-0.01, max(max(rolling_rates) * 1.2, alpha * 3))

    # Bottom panel: Lambda trajectory
    ax2.step(builds, lambda_vals, where='post',
             color=PAPER_COLORS["conformal"], linewidth=2)
    ax2.set_xlabel('Build Index')
    ax2.set_ylabel(r'$\hat{\lambda}$')
    ax2.set_ylim(min(lambda_vals) * 0.9, max(lambda_vals) * 1.05)

    plt.tight_layout()
    fig_path = output_dir / f"fig2_drift_timeline_{project_key}.png"
    fig.savefig(str(fig_path), dpi=dpi)
    plt.close(fig)
    print(f"  Saved: {fig_path.name}")


def figure_pareto_frontier(project_key, output_dir, dpi=300):
    """Figure 3: Cost-Recall Pareto Frontier.

    Scatter plot of fault recall vs test reduction rate for all methods.
    Points closer to top-right (high recall, high reduction) are Pareto optimal.
    """
    if not HAS_MATPLOTLIB:
        print("  [SKIP] Pareto frontier figure (no matplotlib)")
        return

    project_folder = PROJECTS[project_key]["folder"]
    eval_path = project_folder / "evaluation_results.json"
    if not eval_path.exists():
        print(f"  [SKIP] No evaluation_results.json for {project_key}")
        return

    with open(eval_path, encoding="utf-8") as f:
        eval_data = json.load(f)

    methods = eval_data.get("methods", [])
    if not methods:
        print(f"  [SKIP] No methods in evaluation results for {project_key}")
        return

    fig, ax = plt.subplots(figsize=(6, 5))

    for m in methods:
        name = m["method"]
        recall = m.get("fault_recall", 0) * 100
        reduction = m.get("test_reduction_rate", 0) * 100

        # Get color and label
        label, color = METHOD_LABELS.get(name, (name, "#333333"))

        # Match conformal entries dynamically
        if "Conformal" in name or "conformal" in name.lower():
            label = "Conformal"
            color = PAPER_COLORS["conformal"]

        ax.scatter(reduction, recall, s=150, c=color, edgecolors='black',
                   linewidths=1, zorder=5, label=label)

        # Annotate
        offset = (5, 5) if reduction < 70 else (-50, 5)
        ax.annotate(label, (reduction, recall), textcoords="offset points",
                    xytext=offset, fontsize=8, ha='left')

    # Ideal corner highlight
    ax.axhspan(95, 105, alpha=0.05, color='green')
    ax.axvspan(30, 100, alpha=0.05, color='blue')

    ax.set_xlabel('Test Reduction Rate (%)')
    ax.set_ylabel('Fault Recall (%)')
    ax.set_title(f'Cost-Recall Trade-off ({project_key})')
    ax.set_xlim(-5, 100)
    ax.set_ylim(-5, 105)

    # Legend
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax.legend(by_label.values(), by_label.keys(), loc='lower left',
              frameon=True, fancybox=True)

    fig_path = output_dir / f"fig3_pareto_{project_key}.png"
    fig.savefig(str(fig_path), dpi=dpi)
    plt.close(fig)
    print(f"  Saved: {fig_path.name}")


def figure_rho_sensitivity(project_key, output_dir, dpi=300):
    """Figure 4: Rho Sensitivity Analysis.

    Dual-axis plot showing lambda_hat variance (left) and N_eff (right)
    across rho candidates from the parameter sweep.
    """
    if not HAS_MATPLOTLIB:
        print("  [SKIP] Rho sensitivity figure (no matplotlib)")
        return

    project_folder = PROJECTS[project_key]["folder"]
    rho_path = project_folder / "rho_sweep_results.json"
    if not rho_path.exists():
        print(f"  [SKIP] No rho_sweep_results.json for {project_key}")
        return

    with open(rho_path, encoding="utf-8") as f:
        rho_data = json.load(f)

    sweep = rho_data.get("sweep_results", [])
    selected_rho = rho_data.get("selected_rho", 0.995)

    if not sweep:
        print(f"  [SKIP] Empty rho sweep for {project_key}")
        return

    rhos = [r["rho"] for r in sweep]
    variances = [r["cv"]["variance"] for r in sweep]
    n_effs = [r["n_eff"] for r in sweep]

    fig, ax1 = plt.subplots(figsize=(6, 4))
    ax2 = ax1.twinx()

    # Variance (left axis)
    line1, = ax1.plot(rhos, variances, 'o-', color=PAPER_COLORS["conformal"],
                      linewidth=2, markersize=8, label=r'$\mathrm{Var}(\hat{\lambda})$')
    ax1.set_ylabel(r'$\mathrm{Var}(\hat{\lambda})$ across temporal folds',
                   color=PAPER_COLORS["conformal"])
    ax1.tick_params(axis='y', labelcolor=PAPER_COLORS["conformal"])

    # N_eff (right axis)
    line2, = ax2.plot(rhos, n_effs, 's--', color=PAPER_COLORS["tcp_ci"],
                      linewidth=2, markersize=8, label=r'$N_{\mathrm{eff}}$')
    ax2.set_ylabel(r'$N_{\mathrm{eff}}$ (effective sample size)',
                   color=PAPER_COLORS["tcp_ci"])
    ax2.tick_params(axis='y', labelcolor=PAPER_COLORS["tcp_ci"])

    # Highlight selected rho
    ax1.axvline(x=selected_rho, color='green', linestyle=':', linewidth=2,
                alpha=0.7, label=rf'$\rho^* = {selected_rho}$')

    ax1.set_xlabel(r'Decay Parameter $\rho$')
    ax1.set_title(rf'$\rho$ Sensitivity Analysis ({project_key})')

    # Combined legend
    lines = [line1, line2]
    labels = [l.get_label() for l in lines]
    ax1.legend(lines, labels, loc='upper left', frameon=True)

    plt.tight_layout()
    fig_path = output_dir / f"fig4_rho_sensitivity_{project_key}.png"
    fig.savefig(str(fig_path), dpi=dpi)
    plt.close(fig)
    print(f"  Saved: {fig_path.name}")


# ── LaTeX Table Generators ──

def latex_offline_cost_table():
    """Table 1: Offline Indexing Cost Breakdown."""
    latex = r"""
\begin{table}[t]
\centering
\caption{Offline Indexing Cost Breakdown (Verified Google Billing)}
\label{tab:offline_cost}
\begin{tabular}{lllrr}
\toprule
\textbf{Step} & \textbf{Project} & \textbf{Model} & \textbf{Volume} & \textbf{Cost (\rupee)} \\
\midrule
Summaries & Log4j2 & gemini-3.5-flash & 1{,}064 tests & 250.16 \\
Embeddings & Log4j2 & gemini-embedding-2 & 1{,}064 vectors & 2.43 \\
Summaries & ShardingSphere & gemini-3.5-flash & 2{,}161 tests & 487.12 \\
Embeddings & ShardingSphere & gemini-embedding-2 & 2{,}161 vectors & 4.18 \\
Summaries & Sling & gemini-3.6-flash & 1{,}290 tests & 140.82 \\
Embeddings & Sling & gemini-embedding-2 & 1{,}290 vectors & 2.49 \\
\midrule
\multicolumn{4}{l}{\textbf{Total Offline Indexing}} & \textbf{887.20} \\
\bottomrule
\end{tabular}
\end{table}
"""
    return latex.strip()


def latex_benchmark_table(project_key):
    """Table 2: Comprehensive Multi-Dimensional Benchmark."""
    project_folder = PROJECTS[project_key]["folder"]
    eval_path = project_folder / "evaluation_results.json"
    if not eval_path.exists():
        return f"% No evaluation_results.json for {project_key}"

    with open(eval_path, encoding="utf-8") as f:
        eval_data = json.load(f)

    methods = eval_data.get("methods", [])
    total_builds = eval_data.get("evaluation_builds", 0)

    rows = []
    for m in methods:
        name = m["method"].replace("_", r"\_")
        recall = f"{m.get('fault_recall', 0)*100:.1f}\\%"
        apfd = f"{m.get('apfd', {}).get('mean', 0):.4f}"
        reduction = f"{m.get('test_reduction_rate', 0)*100:.1f}\\%"
        r10 = f"{m.get('recall_at_budget', {}).get('recall_at_10pct', 0)*100:.1f}\\%"
        r20 = f"{m.get('recall_at_budget', {}).get('recall_at_20pct', 0)*100:.1f}\\%"
        net = f"\\${m.get('cost_benefit', {}).get('net_savings_usd', 0):.2f}"
        rows.append(f"        {name} & {recall} & {apfd} & {reduction} & {r10} & {r20} & {net} \\\\")

    rows_str = "\n".join(rows)

    latex = rf"""
\begin{{table}}[t]
\centering
\caption{{Comprehensive Benchmark --- {project_key} ({total_builds} builds)}}
\label{{tab:benchmark_{project_key}}}
\begin{{tabular}}{{lcccccc}}
\toprule
\textbf{{Method}} & \textbf{{Recall}} & \textbf{{APFD}} & \textbf{{Reduction}} & \textbf{{R@10\%}} & \textbf{{R@20\%}} & \textbf{{Net \$}} \\
\midrule
{rows_str}
\bottomrule
\end{{tabular}}
\end{{table}}
"""
    return latex.strip()


def latex_impact_table(project_key):
    """Table 3: Extended Impact Metrics."""
    project_folder = PROJECTS[project_key]["folder"]
    cost_path = project_folder / "cost_analysis_results.json"
    if not cost_path.exists():
        return f"% No cost_analysis_results.json for {project_key}"

    with open(cost_path, encoding="utf-8") as f:
        cost_data = json.load(f)

    rows = []
    for m in cost_data.get("methods", []):
        name = m["method"].replace("_", r"\_")
        brk = m.get("break_even", {}).get("break_even_builds", "N/A")
        if isinstance(brk, float) and brk == float('inf'):
            brk = "N/A"
        else:
            brk = f"{brk:.0f}"
        lat = f"{m.get('latency', {}).get('avg_minutes_saved_per_build', 0):.1f}"
        co2 = f"{m.get('carbon', {}).get('annual_co2e_avoided_kg', 0):.1f}"
        roi = f"\\${m.get('roi', {}).get('cumulative_3_year_usd', 0):.0f}"
        rows.append(f"        {name} & {brk} & {lat} & {co2} & {roi} \\\\")

    rows_str = "\n".join(rows)

    latex = rf"""
\begin{{table}}[t]
\centering
\caption{{Extended Impact Metrics --- {project_key}}}
\label{{tab:impact_{project_key}}}
\begin{{tabular}}{{lcccc}}
\toprule
\textbf{{Method}} & \textbf{{Break-Even}} & \textbf{{Min/Build}} & \textbf{{CO$_2$e/yr (kg)}} & \textbf{{3-yr ROI}} \\
\midrule
{rows_str}
\bottomrule
\end{{tabular}}
\end{{table}}
"""
    return latex.strip()


def run_visualizations(project_key, dpi=300):
    """Generate all figures and LaTeX tables for a project."""
    project_folder = PROJECTS[project_key]["folder"]
    output_dir = project_folder / "paper_figures"
    output_dir.mkdir(exist_ok=True)

    setup_paper_style()

    print(f"\n{'='*60}")
    print(f"Generating Publication Figures & Tables: {project_key}")
    print(f"{'='*60}")

    # ── Figures ──
    print("\n--- Figures ---")
    figure_risk_curve(project_key, output_dir, dpi)
    figure_drift_timeline(project_key, output_dir, dpi)
    figure_pareto_frontier(project_key, output_dir, dpi)
    figure_rho_sensitivity(project_key, output_dir, dpi)

    # ── LaTeX Tables ──
    print("\n--- LaTeX Tables ---")
    tables = {
        "table1_offline_cost.tex": latex_offline_cost_table(),
        f"table2_benchmark_{project_key}.tex": latex_benchmark_table(project_key),
        f"table3_impact_{project_key}.tex": latex_impact_table(project_key),
    }

    for filename, content in tables.items():
        table_path = output_dir / filename
        with open(table_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  Saved: {filename}")

    print(f"\nAll outputs saved to {output_dir}")
    return output_dir


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project", choices=list(PROJECTS.keys()),
                        help="Project key")
    parser.add_argument("--dpi", type=int, default=300,
                        help="Figure DPI (default: 300)")
    args = parser.parse_args()

    run_visualizations(args.project, args.dpi)
