"""
Step 3: Online Commit-Aware Semantic Selector.

At build/PR time, assesses test relevance by comparing the incoming
commit's diff against all pre-indexed test embeddings:

    1. Embed the commit diff using gemini-embedding-2
    2. Compute cosine similarity against every test embedding
    3. Triage tests into High-Confidence Relevant / Uncertain / Skip tiers

The selector can operate in two modes:
    - Online (API): Calls gemini-embedding-2 to embed the diff in real time.
    - Offline (precomputed): Loads pre-embedded diffs for batch evaluation.

Usage:
    python -m src.online_selection.semantic_selector log4j2 --slice slice_3_eval.jsonl
    python -m src.online_selection.semantic_selector shardingsphere --tau-high 0.7 --tau-low 0.3
"""
import json
import argparse
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*automatic function calling.*")

# pyrefly: ignore [missing-import]
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.config import PROJECTS, EMBEDDING_MODEL, GEMINI_API_KEY


# ── Default thresholds ──
DEFAULT_TAU_HIGH = 0.65   # Above this → select immediately
DEFAULT_TAU_LOW = 0.35    # Below this → skip immediately
# Between tau_low and tau_high → uncertain band


class SemanticSelector:
    """Commit-aware test selection via embedding cosine similarity.

    Pre-loads the test embedding matrix and test index once, then
    scores any incoming commit diff against all tests in O(n) via
    a single matrix-vector multiply.
    """

    def __init__(self, project_key, tau_high=DEFAULT_TAU_HIGH,
                 tau_low=DEFAULT_TAU_LOW, use_api=True):
        """
        Args:
            project_key: Key into PROJECTS config (e.g. "log4j2").
            tau_high: Cosine similarity threshold above which tests
                      are selected immediately.
            tau_low:  Cosine similarity threshold below which tests
                      are skipped immediately.
            use_api:  If True, calls the Gemini API to embed diffs.
                      If False, expects pre-computed diff embeddings.
        """
        self.project_key = project_key
        self.tau_high = tau_high
        self.tau_low = tau_low
        self.use_api = use_api

        proj = PROJECTS[project_key]
        self.project_folder = proj["folder"]

        # Load test embeddings (precomputed in Phase 1)
        embeddings_path = self.project_folder / "test_embeddings.npz"
        if not embeddings_path.exists():
            raise FileNotFoundError(f"Test embeddings not found: {embeddings_path}")

        data = np.load(str(embeddings_path))
        self.test_embeddings = data["embeddings"].astype(np.float32)  # shape: (N, D)

        # L2-normalize for fast cosine similarity via dot product
        norms = np.linalg.norm(self.test_embeddings, axis=1, keepdims=True)
        self.test_embeddings_normed = self.test_embeddings / (norms + 1e-9)

        # Load test index (path + summary for each embedding row)
        index_path = self.project_folder / "test_index.jsonl"
        if not index_path.exists():
            raise FileNotFoundError(f"Test index not found: {index_path}")

        self.test_index = []
        with open(index_path, encoding="utf-8") as f:
            for line in f:
                self.test_index.append(json.loads(line))

        assert len(self.test_index) == self.test_embeddings.shape[0], \
            f"Index ({len(self.test_index)}) != embeddings ({self.test_embeddings.shape[0]})"

        # Build path -> index lookup
        self.path_to_idx = {entry["test_path"]: entry["index"] for entry in self.test_index}

        # Diff embeddings cache
        self.diff_cache_path = self.project_folder / "diff_embeddings_cache.npz"
        self.diff_cache = {}
        if self.diff_cache_path.exists():
            try:
                data_cache = np.load(str(self.diff_cache_path))
                for k in data_cache.files:
                    self.diff_cache[k] = data_cache[k]
                print(f"  Loaded {len(self.diff_cache)} cached diff embeddings from {self.diff_cache_path.name}")
            except Exception as e:
                print(f"  Warning: could not read diff cache: {e}")

        # Setup API client if needed
        self._client = None
        if self.use_api:
            self._init_api_client()

        print(f"SemanticSelector initialized for {project_key}: "
              f"{len(self.test_index)} tests, D={self.test_embeddings.shape[1]}, "
              f"tau_high={tau_high}, tau_low={tau_low}")

    def _init_api_client(self):
        """Initialize the Gemini API client for diff embedding."""
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not set. Required for online mode.")

        from google import genai
        self._client = genai.Client(api_key=GEMINI_API_KEY)

    def save_diff_cache(self):
        """Save cached diff embeddings to disk."""
        if self.diff_cache:
            try:
                np.savez_compressed(str(self.diff_cache_path), **self.diff_cache)
            except Exception as e:
                print(f"  Warning: could not save diff cache: {e}")

    def embed_diff(self, diff_text, cache_key=None, max_chars=15000):
        """Embed a commit diff text using the Gemini Embedding API or cache.

        Args:
            diff_text: The unified diff string from the commit.
            cache_key: Optional cache identifier (commit_sha or build_id).
            max_chars: Truncate diff to this length to stay within token limits.

        Returns:
            numpy array of shape (D,) — the diff embedding vector.
        """
        if cache_key and str(cache_key) in self.diff_cache:
            return self.diff_cache[str(cache_key)]

        if not diff_text or not diff_text.strip():
            emb = np.zeros(self.test_embeddings.shape[1], dtype=np.float32)
            if cache_key:
                self.diff_cache[str(cache_key)] = emb
            return emb

        # Truncate very long diffs to stay within token limits
        if len(diff_text) > max_chars:
            diff_text = diff_text[:max_chars] + "\n... [truncated]"

        if self.use_api and self._client:
            from google.genai import types

            content = types.Content(parts=[types.Part.from_text(text=diff_text)])

            for attempt in range(3):
                try:
                    result = self._client.models.embed_content(
                        model=EMBEDDING_MODEL,
                        contents=[content],
                    )
                    break
                except Exception as e:
                    if attempt < 2:
                        wait = 5 * (attempt + 1)
                        print(f"    Embedding retry in {wait}s: {str(e)[:80]}")
                        time.sleep(wait)
                    else:
                        raise

            emb = np.array(result.embeddings[0].values, dtype=np.float32)
            if cache_key:
                self.diff_cache[str(cache_key)] = emb
            return emb
        else:
            raise RuntimeError("API client not available and diff not cached. Set use_api=True.")

    def precompute_diffs_for_records(self, records, batch_size=50):
        """Precompute and cache diff embeddings for a list of build records in batches."""
        to_embed = []
        for rec in records:
            key = str(rec.get("commit_sha") or rec.get("build_id"))
            if key not in self.diff_cache:
                diff_text = rec.get("diff_text", "")
                to_embed.append((key, diff_text))

        if not to_embed:
            return

        if not self.use_api or not self._client:
            print(f"  Warning: Cannot precompute {len(to_embed)} diffs without API client.")
            return

        print(f"  Precomputing embeddings for {len(to_embed)} commit diffs in batches of {batch_size}...")
        from google.genai import types

        for i in range(0, len(to_embed), batch_size):
            batch = to_embed[i:i + batch_size]
            batch_contents = []
            for key, diff_text in batch:
                dt = diff_text.strip() if diff_text else ""
                if len(dt) > 15000:
                    dt = dt[:15000] + "\n... [truncated]"
                if not dt:
                    dt = "Empty commit diff"
                batch_contents.append(types.Content(parts=[types.Part.from_text(text=dt)]))

            for attempt in range(3):
                try:
                    result = self._client.models.embed_content(
                        model=EMBEDDING_MODEL,
                        contents=batch_contents,
                    )
                    break
                except Exception as e:
                    if attempt < 2:
                        wait = 5 * (attempt + 1)
                        print(f"    Batch retry in {wait}s: {str(e)[:80]}")
                        time.sleep(wait)
                    else:
                        raise

            for (key, _), emb_obj in zip(batch, result.embeddings):
                self.diff_cache[key] = np.array(emb_obj.values, dtype=np.float32)

            print(f"    Embedded {min(i + batch_size, len(to_embed))}/{len(to_embed)} diffs...")
            time.sleep(0.5)

        self.save_diff_cache()
        print(f"  Diff cache updated and saved ({len(self.diff_cache)} total).")

    def score_tests(self, diff_embedding):
        """Compute cosine similarity between a diff embedding and all test embeddings.

        Args:
            diff_embedding: numpy array of shape (D,).

        Returns:
            numpy array of shape (N,) — cosine similarity scores.
        """
        # Normalize the diff embedding
        norm = np.linalg.norm(diff_embedding)
        if norm < 1e-9:
            return np.zeros(len(self.test_index), dtype=np.float32)
        diff_normed = diff_embedding / norm

        # Single matrix-vector multiply for all N tests
        similarities = self.test_embeddings_normed @ diff_normed
        return similarities

    def triage(self, similarities):
        """Partition tests into three tiers based on similarity scores.

        Args:
            similarities: numpy array of shape (N,) — cosine scores.

        Returns:
            dict with:
                "high_confidence": [(idx, path, sim), ...]  — select immediately
                "uncertain":      [(idx, path, sim), ...]  — needs verification
                "skip":           [(idx, path, sim), ...]  — skip immediately
        """
        high_confidence = []
        uncertain = []
        skip = []

        for i, sim in enumerate(similarities):
            entry = self.test_index[i]
            item = (i, entry["test_path"], float(sim))

            if sim >= self.tau_high:
                high_confidence.append(item)
            elif sim <= self.tau_low:
                skip.append(item)
            else:
                uncertain.append(item)

        # Sort each tier by similarity (descending)
        high_confidence.sort(key=lambda x: x[2], reverse=True)
        uncertain.sort(key=lambda x: x[2], reverse=True)
        skip.sort(key=lambda x: x[2], reverse=True)

        return {
            "high_confidence": high_confidence,
            "uncertain": uncertain,
            "skip": skip,
        }

    def select(self, build_record, include_uncertain=True):
        """Full selection pipeline for one build record.

        This is the main entry point matching the baseline interface.

        Args:
            build_record: A single record from complete_dataset.jsonl.
            include_uncertain: If True, uncertain-band tests are selected
                               (conservative). If False, they are skipped.

        Returns:
            dict with selected_tests, skipped_tests, scores, triage_summary.
        """
        diff_text = build_record.get("diff_text", "")
        cache_key = str(build_record.get("commit_sha") or build_record.get("build_id"))
        diff_emb = self.embed_diff(diff_text, cache_key=cache_key)
        similarities = self.score_tests(diff_emb)

        triage_result = self.triage(similarities)

        # Build a path -> similarity score map for tests in this build
        test_names = [t["testName"] for t in build_record.get("test_outcomes", []) if t.get("testName")]
        scores = {}
        for name in test_names:
            idx = self.path_to_idx.get(name)
            if idx is not None and idx < len(similarities):
                scores[name] = float(similarities[idx])
            else:
                # Test not in our index — conservatively give it a neutral score
                scores[name] = float(self.tau_high)  # default to select

        selected = []
        skipped = []
        for name in test_names:
            sim = scores.get(name, self.tau_high)
            if sim >= self.tau_high:
                selected.append(name)
            elif sim <= self.tau_low:
                skipped.append(name)
            elif include_uncertain:
                selected.append(name)
            else:
                skipped.append(name)

        return {
            "selected_tests": selected,
            "skipped_tests": skipped,
            "scores": scores,
            "triage_summary": {
                "high_confidence": len(triage_result["high_confidence"]),
                "uncertain": len(triage_result["uncertain"]),
                "skip": len(triage_result["skip"]),
            },
        }


def batch_evaluate(project_key, slice_path, tau_high, tau_low, limit=None):
    """Run the semantic selector on an entire evaluation slice.

    Precomputes diff embeddings in fast batches, then performs vector
    evaluation in memory.
    """
    proj = PROJECTS[project_key]
    project_folder = proj["folder"]

    eval_path = Path(slice_path) if slice_path else project_folder / "slice_3_eval.jsonl"
    if not eval_path.exists():
        print(f"ERROR: Evaluation slice not found: {eval_path}")
        sys.exit(1)

    selector = SemanticSelector(project_key, tau_high=tau_high, tau_low=tau_low, use_api=True)

    print(f"\nEvaluating on {eval_path.name}...")
    records = []
    with open(eval_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            records.append(json.loads(line))

    if limit:
        records = records[:limit]
        print(f"  Limited to {limit} builds.")

    # Precompute all diff embeddings in batches
    selector.precompute_diffs_for_records(records)

    total_tests = 0
    total_selected = 0
    total_failures = 0
    caught_failures = 0
    per_build_results = []

    for i, rec in enumerate(records):
        result = selector.select(rec, include_uncertain=True)
        selected_set = set(result["selected_tests"])

        build_failures = 0
        build_caught = 0
        build_tests = 0

        for outcome in rec.get("test_outcomes", []):
            name = outcome.get("testName")
            if not name:
                continue
            build_tests += 1
            is_failure = str(outcome.get("verdict")) in ("1", "2", "3")
            if is_failure:
                build_failures += 1
                total_failures += 1
                if name in selected_set:
                    build_caught += 1
                    caught_failures += 1

        total_tests += build_tests
        total_selected += len(result["selected_tests"])

        per_build_results.append({
            "build_id": rec["build_id"],
            "tests": build_tests,
            "selected": len(result["selected_tests"]),
            "skipped": len(result["skipped_tests"]),
            "failures": build_failures,
            "caught": build_caught,
            "triage": result.get("triage_summary", {}),
        })

        if (i + 1) % 50 == 0:
            print(f"  Processed {i+1}/{len(records)} builds...")

    total_skipped = total_tests - total_selected
    fault_recall = caught_failures / max(1, total_failures)
    test_savings = total_skipped / max(1, total_tests)

    print(f"\n{'=' * 60}")
    print(f"Semantic Selector Results ({project_key})")
    print(f"  tau_high={tau_high}, tau_low={tau_low}")
    print(f"  Builds: {len(records)}")
    print(f"  Tests: {total_tests:,}  Selected: {total_selected:,}  Skipped: {total_skipped:,}")
    print(f"  Failures: {total_failures:,}  Caught: {caught_failures:,}")
    print(f"  Fault Recall: {fault_recall:.4%}")
    print(f"  Test Savings: {test_savings:.4%}")
    print(f"{'=' * 60}")

    # Save results
    results = {
        "project": project_key,
        "tau_high": tau_high,
        "tau_low": tau_low,
        "total_builds": len(records),
        "total_tests": total_tests,
        "total_selected": total_selected,
        "total_skipped": total_skipped,
        "total_failures": total_failures,
        "caught_failures": caught_failures,
        "fault_recall": round(fault_recall, 6),
        "test_savings_rate": round(test_savings, 6),
        "per_build": per_build_results,
    }

    results_path = project_folder / "semantic_selector_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Results saved to {results_path}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", choices=list(PROJECTS.keys()), help="Project key")
    parser.add_argument("--slice", help="Evaluation slice JSONL (default: slice_3_eval.jsonl)")
    parser.add_argument("--tau-high", type=float, default=DEFAULT_TAU_HIGH,
                        help=f"High confidence threshold (default: {DEFAULT_TAU_HIGH})")
    parser.add_argument("--tau-low", type=float, default=DEFAULT_TAU_LOW,
                        help=f"Low confidence threshold (default: {DEFAULT_TAU_LOW})")
    parser.add_argument("--limit", type=int, help="Process only first N builds (for testing)")
    args = parser.parse_args()

    batch_evaluate(args.project, args.slice, args.tau_high, args.tau_low, args.limit)
