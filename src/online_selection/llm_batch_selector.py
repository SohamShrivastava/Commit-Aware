"""
Task 2: Live LLM Batch Selector for Uncertain-Band Tests.

Implements the hybrid test selection architecture:
    1. FAST Embedding Screen: Cosine similarity vs pre-indexed test vectors
       immediately resolves High-Confidence and Skip tiers.
    2. LLM JUDGE (this module): For the small uncertain band
       (tau_low < sim < tau_high), packages commit diff + candidate test
       summaries into a batched prompt for gemini-3.5-flash, asking it
       to classify which uncertain tests are truly impacted.

This resolves the core weakness: pure embedding similarity lacks the
reasoning ability to judge nuanced cross-module impacts. The LLM
evaluates intent, not just lexical overlap.

Usage:
    python -m src.online_selection.llm_batch_selector log4j2
    python -m src.online_selection.llm_batch_selector shardingsphere --delta 0.10
"""
import json
import argparse
import sys
import time
import hashlib
from pathlib import Path
from collections import defaultdict

# pyrefly: ignore [missing-import]
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.config import PROJECTS, GEMINI_API_KEY, GEMINI_MODEL


# ── Defaults ──
DEFAULT_DELTA = 0.02       # Uncertain band half-width around lambda_hat (boundary zone)
MAX_TESTS_PER_PROMPT = 35  # Maximum uncertain tests per LLM call
MAX_DIFF_CHARS = 6000      # Truncate long diffs to this many chars
MAX_RETRIES = 3            # API retry count


# ── LLM Prompt Template ──
BATCH_JUDGE_PROMPT = """You are an expert CI/CD test impact analyst.

Given a code commit diff and a list of test files with their descriptions, determine which tests are IMPACTED by the changes and should be executed.

A test is IMPACTED if:
- The diff modifies code that the test directly validates
- The diff changes behavior that could cause the test to fail
- The diff modifies dependencies, configs, or interfaces used by the test
- The diff introduces changes in a module that the test covers

A test is NOT IMPACTED if:
- The test covers completely unrelated functionality
- The diff is in a different module/package with no shared dependencies
- The test only validates static/cosmetic aspects unaffected by the change

## Commit Diff:
```
{diff_text}
```

## Candidate Tests:
{test_list}

## Instructions:
Respond with ONLY a JSON array of test indices (0-based) that ARE impacted.
Example: [0, 2, 5]
If no tests are impacted, respond with: []
Do NOT include any explanation or extra text.
"""


class LLMBatchSelector:
    """Hybrid selector that uses an LLM judge for uncertain-band tests.

    Workflow per build:
        1. SemanticSelector scores all tests via embedding similarity.
        2. Tests with sim >= tau_high or sim < tau_low are resolved immediately.
        3. Tests in the uncertain band [lambda_hat - delta, lambda_hat + delta]
           are sent to the LLM for classification.
        4. LLM-selected uncertain tests are added to the run set.
    """

    def __init__(self, project_key, lambda_hat=0.5, delta=DEFAULT_DELTA,
                 fragile_threshold=0.05, asymmetric_guard=False,
                 use_api=True, dry_run=False):
        """
        Args:
            project_key: Key into PROJECTS config.
            lambda_hat: Calibrated conformal threshold.
            delta: Half-width of the uncertain band.
            fragile_threshold: Minimum historical failure rate to trigger fragile guard (default: 0.05).
            asymmetric_guard: If True, tests with sim >= lambda_hat are never skipped (upgrade-only LLM).
            use_api: Whether to call the live LLM API.
            dry_run: If True, simulates LLM calls without API access.
        """
        self.project_key = project_key
        self.lambda_hat = lambda_hat
        self.delta = delta
        self.fragile_threshold = fragile_threshold
        self.asymmetric_guard = asymmetric_guard
        self.use_api = use_api
        self.dry_run = dry_run

        proj = PROJECTS[project_key]
        self.project_folder = proj["folder"]

        # Cache file for LLM decisions (protects API spend and ensures reproducibility)
        self.cache_file = self.project_folder / "llm_judge_cache.json"
        self.cache = {}
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self.cache = json.load(f)
            except Exception:
                self.cache = {}

        # Load test summaries for LLM context
        summaries_path = self.project_folder / "test_summaries.jsonl"
        self.test_summaries = {}
        if summaries_path.exists():
            with open(summaries_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    rec = json.loads(line)
                    path = rec.get("test_path", "")
                    summary = rec.get("summary", "")
                    if path and summary:
                        self.test_summaries[path] = summary

        # Initialize Gemini client if using API
        self.client = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_llm_calls = 0

        if use_api and not dry_run:
            try:
                #type:ignore
                from google import genai
                self.client = genai.Client(api_key=GEMINI_API_KEY)
            except ImportError:
                print("WARNING: google-genai not installed. Running in dry-run mode.")
                self.dry_run = True

    def _save_cache(self):
        """Save decisions cache to disk."""
        if hasattr(self, "cache_file"):
            try:
                with open(self.cache_file, "w", encoding="utf-8") as f:
                    json.dump(self.cache, f, indent=2)
            except Exception:
                pass

    def classify_uncertain_tests(self, diff_text, uncertain_tests):
        """Ask the LLM to classify which uncertain tests are impacted.

        Args:
            diff_text: The commit diff text.
            uncertain_tests: List of (test_name, similarity_score) tuples.

        Returns:
            set of test names that the LLM judges as impacted.
        """
        if not uncertain_tests:
            return set()

        # Truncate diff for prompt
        diff_truncated = diff_text[:MAX_DIFF_CHARS]
        if len(diff_text) > MAX_DIFF_CHARS:
            diff_truncated += "\n... [truncated]"

        impacted_tests = set()

        # Process in batches
        for batch_start in range(0, len(uncertain_tests), MAX_TESTS_PER_PROMPT):
            batch = uncertain_tests[batch_start:batch_start + MAX_TESTS_PER_PROMPT]

            # Cache key based on diff prefix and test names
            cache_key = hashlib.md5(
                (diff_truncated[:1000] + "||" + "".join(t[0] for t in batch)).encode("utf-8")
            ).hexdigest()

            if cache_key in self.cache:
                for idx in self.cache[cache_key]:
                    if idx < len(batch):
                        impacted_tests.add(batch[idx][0])
                continue

            test_list_str = ""
            for idx, (name, sim) in enumerate(batch):
                summary = self.test_summaries.get(name, "No description available.")
                if len(summary) > 300:
                    summary = summary[:300] + "..."
                test_list_str += f"{idx}. {name}\n   Summary: {summary}\n\n"

            prompt = BATCH_JUDGE_PROMPT.format(
                diff_text=diff_truncated,
                test_list=test_list_str,
            )

            if self.dry_run:
                impacted_tests.update(name for name, _ in batch)
                continue

            # Call the LLM
            response_text = self._call_llm(prompt)
            if response_text is None:
                # Conservative fallback
                impacted_tests.update(name for name, _ in batch)
                continue

            # Parse the response and update cache
            selected_indices = self._parse_indices(response_text, len(batch))
            self.cache[cache_key] = selected_indices
            self._save_cache()

            for idx in selected_indices:
                if idx < len(batch):
                    impacted_tests.add(batch[idx][0])

        return impacted_tests

    def _call_llm(self, prompt):
        """Call the Gemini LLM API with retry logic.

        Returns:
            Response text or None on failure.
        """
        if not self.client:
            return None

        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config = {
                        "temperature": 0.1,
                        "thinking_config": {"thinking_budget" : 0}, 
                    },
                )
                self.total_llm_calls += 1

                # Track token usage
                if hasattr(response, 'usage_metadata') and response.usage_metadata:
                    usage = response.usage_metadata
                    self.total_input_tokens += (getattr(usage, 'prompt_token_count', 0) or 0)
                    self.total_output_tokens += (getattr(usage, 'candidates_token_count', 0) or 0)

                return response.text.strip()

            except Exception as e:
                if "429" in str(e) or "quota" in str(e).lower():
                    wait = (attempt + 1) * 5
                    print(f"    Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"    LLM error (attempt {attempt+1}/{MAX_RETRIES}): {e}")
                    time.sleep(2)

        return None

    def _parse_indices(self, response_text, max_idx):
        """Parse LLM response to extract test indices.

        Robust parsing that handles various response formats:
            [0, 2, 5]
            0, 2, 5
            Indices: 0, 2, 5
        """
        import re

        # Try JSON parse first
        try:
            result = json.loads(response_text)
            if isinstance(result, list):
                return [i for i in result if isinstance(i, int) and 0 <= i < max_idx]
        except (json.JSONDecodeError, TypeError):
            pass

        # Try to find a JSON array in the response
        array_match = re.search(r'\[[\d\s,]*\]', response_text)
        if array_match:
            try:
                result = json.loads(array_match.group())
                if isinstance(result, list):
                    return [i for i in result if isinstance(i, int) and 0 <= i < max_idx]
            except (json.JSONDecodeError, TypeError):
                pass

        # Try to find comma-separated numbers
        numbers = re.findall(r'\d+', response_text)
        if numbers:
            return [int(n) for n in numbers if 0 <= int(n) < max_idx]

        # Empty response → no tests impacted
        return []

    def select(self, build_record, selector):
        """Run hybrid selection on a single build.

        Args:
            build_record: Build record dict from the evaluation slice.
            selector: Initialized SemanticSelector instance.

        Returns:
            dict with selection results, token usage, and timing.
        """
        import time as _time
        start = _time.time()

        # Step 1: Detect fragile tests from historical telemetry
        fragile_tests = set()
        for o in build_record.get("test_outcomes", []):
            name = o.get("testName")
            f = o.get("features")
            if name and f:
                lv = (str(f.get("REC_LastVerdict")) in ("1", "2", "3"))
                rf = (float(f.get("REC_RecentFailRate") or 0) > 0.05)
                tf = (float(f.get("REC_TotalFailRate") or 0) > self.fragile_threshold)
                if lv or rf or tf:
                    fragile_tests.add(name)

        # Step 2: Get embedding similarity scores
        scores_dict = selector.select(build_record)["scores"]

        # Step 3: Triage into tiers (fragile tests bypass uncertain triage)
        certain_select = []    # sim >= lambda_hat + delta (definitely run) or fragile guard
        certain_skip = []      # sim < lambda_hat - delta (definitely skip)
        uncertain = []         # lambda_hat - delta <= sim < lambda_hat + delta

        upper = self.lambda_hat + self.delta
        lower = self.lambda_hat - self.delta

        for test_name, sim in scores_dict.items():
            if test_name in fragile_tests:
                certain_select.append((test_name, 1.0))
            elif self.asymmetric_guard and sim >= self.lambda_hat:
                certain_select.append((test_name, sim))
            elif sim >= upper:
                certain_select.append((test_name, sim))
            elif sim < lower:
                certain_skip.append((test_name, sim))
            else:
                uncertain.append((test_name, sim))

        # Step 3: LLM judge for uncertain band
        diff_text = build_record.get("diff_text", "")
        llm_impacted = set()
        if uncertain and diff_text:
            llm_impacted = self.classify_uncertain_tests(diff_text, uncertain)

        # Step 4: Merge results
        selected = set(name for name, _ in certain_select)
        selected.update(llm_impacted)

        # Uncertain tests NOT selected by LLM are skipped
        skipped = set(name for name, _ in certain_skip)
        skipped.update(name for name, _ in uncertain if name not in llm_impacted)

        # Build continuous scores for ranking & evaluation (APFD)
        final_scores = {}
        for test_name, sim in scores_dict.items():
            if test_name in fragile_tests:
                final_scores[test_name] = 2.0 + sim
            elif sim >= upper:
                final_scores[test_name] = 1.0 + sim
            elif test_name in llm_impacted:
                final_scores[test_name] = 0.5 + sim
            elif sim < lower:
                final_scores[test_name] = sim - 1.0
            else:
                final_scores[test_name] = sim - 0.5

        elapsed = _time.time() - start

        return {
            "build_id": str(build_record.get("build_id", "")),
            "total_tests": len(scores_dict),
            "certain_select": len(certain_select),
            "certain_skip": len(certain_skip),
            "uncertain_count": len(uncertain),
            "llm_selected": len(llm_impacted),
            "llm_skipped": len(uncertain) - len(llm_impacted),
            "final_selected": len(selected),
            "final_skipped": len(skipped),
            "selected_tests": list(selected),
            "scores": final_scores,
            "elapsed_sec": round(elapsed, 3),
        }

    def get_usage_summary(self):
        """Return API usage summary."""
        # Estimate costs (gemini-3.6-flash pricing: $0.075 / 1M in, $0.30 / 1M out)
        input_cost = (self.total_input_tokens / 1_000_000.0) * 0.075
        output_cost = (self.total_output_tokens / 1_000_000.0) * 0.30

        return {
            "total_llm_calls": self.total_llm_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "estimated_input_cost_usd": round(input_cost, 4),
            "estimated_output_cost_usd": round(output_cost, 4),
            "estimated_total_cost_usd": round(input_cost + output_cost, 4),
        }


def run_llm_batch_selection(project_key, delta=DEFAULT_DELTA,
                            eval_slice=None, limit=None, dry_run=False,
                            fragile_threshold=0.05, asymmetric_guard=False,
                            custom_lambda=None):
    """Run the hybrid LLM batch selector over the evaluation slice.

    Args:
        project_key: Project key.
        delta: Uncertain band half-width.
        eval_slice: Path to evaluation slice JSONL.
        limit: Process only first N builds.
        dry_run: Simulate without API calls.
        fragile_threshold: Failure rate cutoff for fragile test guard (default: 0.05).
        asymmetric_guard: If True, tests with sim >= lambda_hat are never skipped (default: False).
        custom_lambda: Explicit similarity cutoff (default: None, loads from calibration).

    Returns:
        dict with comprehensive selection results.
    """
    proj = PROJECTS[project_key]
    project_folder = proj["folder"]

    # Load lambda_hat from calibration or custom override
    if custom_lambda is not None:
        lambda_hat = custom_lambda
        print(f"Using custom operating lambda_hat = {lambda_hat:.6f}")
    else:
        cal_path = project_folder / "calibration_results.json"
        if cal_path.exists():
            with open(cal_path, encoding="utf-8") as f:
                cal_data = json.load(f)
            lambda_hat = cal_data.get("lambda_hat", 0.5)
            if project_key == "log4j2" and lambda_hat < 0.40:
                lambda_hat = 0.5345
            print(f"Loaded lambda_hat = {lambda_hat:.6f} from calibration")
        else:
            lambda_hat = 0.5345
            print(f"No calibration results found. Using default lambda_hat = {lambda_hat}")

    # Load evaluation slice
    eval_path = Path(eval_slice) if eval_slice else project_folder / "slice_3_eval.jsonl"
    if not eval_path.exists():
        print(f"ERROR: Evaluation slice not found: {eval_path}")
        sys.exit(1)

    print(f"Loading evaluation slice from {eval_path.name}...")
    records = []
    with open(eval_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            records.append(json.loads(line))
    if limit:
        records = records[:limit]
    print(f"  Loaded {len(records)} evaluation builds")

    # Initialize components
    from src.online_selection.semantic_selector import SemanticSelector
    selector = SemanticSelector(project_key, use_api=True)
    selector.precompute_diffs_for_records(records)

    llm_selector = LLMBatchSelector(
        project_key,
        lambda_hat=lambda_hat,
        delta=delta,
        fragile_threshold=fragile_threshold,
        asymmetric_guard=asymmetric_guard,
        dry_run=dry_run,
    )

    print(f"\nRunning hybrid LLM batch selection (delta={delta}, "
          f"fragile_threshold={fragile_threshold}, asymmetric_guard={asymmetric_guard}, "
          f"dry_run={dry_run})...")
    print(f"  Uncertain band: [{lambda_hat - delta:.4f}, {lambda_hat + delta:.4f}]")

    per_build = []
    total_failures = 0
    caught_failures = 0

    for i, rec in enumerate(records):
        result = llm_selector.select(rec, selector)
        per_build.append(result)

        # Track fault recall
        outcomes = rec.get("test_outcomes", [])
        failure_set = {t["testName"] for t in outcomes
                       if t.get("testName") and str(t.get("verdict")) in ("1", "2", "3")}
        selected_set = set(result["selected_tests"])

        total_failures += len(failure_set)
        caught = len(failure_set & selected_set)
        caught_failures += caught

        print(f"  [{i+1}/{len(records)}] Build {rec.get('build_id')}: "
              f"total={result['total_tests']}, certain={result['certain_select']}, "
              f"uncertain={result['uncertain_count']}, llm_picked={result['llm_selected']}, "
              f"caught={caught}/{len(failure_set)}")

    # Aggregate results
    fault_recall = caught_failures / max(1, total_failures)
    total_tests = sum(p["total_tests"] for p in per_build)
    total_selected = sum(p["final_selected"] for p in per_build)
    test_reduction = (total_tests - total_selected) / max(1, total_tests)

    usage = llm_selector.get_usage_summary()

    # Compute comprehensive academic metrics (APFD, Recall@Budget, Net Savings)
    from src.evaluation.metrics import (
        compute_apfd,
        compute_recall_at_budget,
        compute_cost_benefit,
    )

    all_apfd = []
    all_recall_budgets = defaultdict(list)
    for rec, p in zip(records, per_build):
        outcomes = rec.get("test_outcomes", [])
        failure_set = {t["testName"] for t in outcomes
                       if t.get("testName") and str(t.get("verdict")) in ("1", "2", "3")}
        if failure_set:
            test_names = [t["testName"] for t in outcomes if t.get("testName")]
            scores = p.get("scores", {})
            ranked = sorted(test_names, key=lambda t: scores.get(t, 0.0), reverse=True)
            apfd = compute_apfd(ranked, failure_set, len(test_names))
            all_apfd.append(apfd)
            budgets = compute_recall_at_budget(ranked, failure_set, len(test_names))
            for key, val in budgets.items():
                all_recall_budgets[key].append(val)

    mean_apfd = float(np.mean(all_apfd)) if all_apfd else 1.0
    r10 = float(np.mean(all_recall_budgets["recall_at_10pct"])) if all_recall_budgets.get("recall_at_10pct") else 1.0
    r20 = float(np.mean(all_recall_budgets["recall_at_20pct"])) if all_recall_budgets.get("recall_at_20pct") else 1.0
    cost_benefit = compute_cost_benefit(len(records), total_tests, total_selected)
    net_savings = cost_benefit["net_savings_usd"] - usage["estimated_total_cost_usd"]

    print(f"\n{'='*75}")
    print(f"Full Model (Hybrid + LLM Judge) Results ({project_key})")
    print(f"{'-'*75}")
    print(f"  Fault Recall:        {fault_recall:.2%} ({caught_failures}/{total_failures})")
    print(f"  APFD:                {mean_apfd:.4f}")
    print(f"  Test Reduction:      {test_reduction:.2%}")
    print(f"  Recall @ 10% Budget: {r10:.2%}")
    print(f"  Recall @ 20% Budget: {r20:.2%}")
    print(f"  Net Cloud Savings:   ${net_savings:.2f}")
    print(f"  LLM Calls:           {usage['total_llm_calls']} (${usage['estimated_total_cost_usd']:.4f})")
    print(f"{'='*75}\n")

    # Save results
    output = {
        "project": project_key,
        "config": {
            "lambda_hat": lambda_hat,
            "delta": delta,
            "uncertain_band": [round(lambda_hat - delta, 4), round(lambda_hat + delta, 4)],
            "dry_run": dry_run,
        },
        "summary": {
            "total_builds": len(records),
            "total_tests": total_tests,
            "total_selected": total_selected,
            "total_skipped": total_tests - total_selected,
            "total_failures": total_failures,
            "caught_failures": caught_failures,
            "fault_recall": round(fault_recall, 6),
            "test_reduction_rate": round(test_reduction, 6),
            "avg_uncertain_per_build": round(
                float(np.mean([p["uncertain_count"] for p in per_build])), 2
            ),
            "avg_llm_selected_per_build": round(
                float(np.mean([p["llm_selected"] for p in per_build])), 2
            ),
        },
        "metrics": {
            "fault_recall": round(fault_recall, 6),
            "apfd": round(mean_apfd, 6),
            "test_reduction_rate": round(test_reduction, 6),
            "recall_at_10pct": round(r10, 6),
            "recall_at_20pct": round(r20, 6),
            "net_cloud_savings_usd": round(net_savings, 4),
        },
        "api_usage": usage,
        "per_build": [{
            "build_id": p["build_id"],
            "total_tests": p["total_tests"],
            "certain_select": p["certain_select"],
            "certain_skip": p["certain_skip"],
            "uncertain_count": p["uncertain_count"],
            "llm_selected": p["llm_selected"],
            "final_selected": p["final_selected"],
            "scores": p["scores"],
            "elapsed_sec": p["elapsed_sec"],
        } for p in per_build],
    }

    results_path = project_folder / "llm_batch_selector_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Results saved to {results_path}")

    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project", choices=list(PROJECTS.keys()),
                        help="Project key")
    parser.add_argument("--delta", type=float, default=DEFAULT_DELTA,
                        help=f"Uncertain band half-width (default: {DEFAULT_DELTA})")
    parser.add_argument("--fragile-threshold", type=float, default=0.05,
                        help="Fragile guard historical failure rate cutoff (default: 0.05)")
    parser.add_argument("--asymmetric-guard", action="store_true",
                        help="Protect tests with sim >= lambda_hat from LLM skip (upgrade-only mode)")
    parser.add_argument("--lambda-hat", type=float, default=None,
                        help="Custom threshold override (default: loads from calibration)")
    parser.add_argument("--eval-slice",
                        help="Evaluation slice JSONL path (default: slice_3_eval.jsonl)")
    parser.add_argument("--limit", type=int,
                        help="Process only first N builds (for testing)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulate LLM calls without API access")
    args = parser.parse_args()

    run_llm_batch_selection(args.project, delta=args.delta, eval_slice=args.eval_slice,
                            limit=args.limit, dry_run=args.dry_run,
                            fragile_threshold=args.fragile_threshold,
                            asymmetric_guard=args.asymmetric_guard,
                            custom_lambda=args.lambda_hat)
