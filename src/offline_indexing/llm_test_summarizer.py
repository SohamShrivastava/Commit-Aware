"""
Step 2: Summarize test files using Google Gemini API.

Reads unique_tests.jsonl, sends ONE test per request to Gemini,
and writes test_summaries.jsonl. Supports resume — re-run to pick up
where it left off.

Usage:
    python -m src.offline_indexing.llm_test_summarizer log4j2
    python -m src.offline_indexing.llm_test_summarizer shardingsphere --limit 20
"""
import json
import time
import argparse
import sys
import warnings
from pathlib import Path

# Suppress the AFC (automatic function calling) warning from google-genai
warnings.filterwarnings("ignore", message=".*automatic function calling.*")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.config import (
    PROJECTS, GEMINI_API_KEY, GEMINI_MODEL,
    GEMINI_RPM, GEMINI_MAX_OUTPUT_TOKENS,
    SUMMARIZE_PROMPT,
)

from google import genai


def setup_gemini():
    """Configure the Gemini client."""
    if not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY not set.")
        print("  Add it to your .env file: GEMINI_API_KEY=your-key-here")
        sys.exit(1)
    client = genai.Client(api_key=GEMINI_API_KEY)
    return client


def summarize_tests(project_key, limit=None, output_path=None, usage_path=None, fresh=False):
    proj = PROJECTS[project_key]
    project_folder = proj["folder"]
    tests_path = project_folder / "unique_tests.jsonl"
    out_path = Path(output_path) if output_path else project_folder / "test_summaries.jsonl"
    usage_path = Path(usage_path) if usage_path else out_path.with_name(
        f"{out_path.stem}_usage.jsonl"
    )

    if fresh and out_path.exists():
        raise FileExistsError(f"Fresh output already exists; choose another path: {out_path}")
    if fresh and usage_path.exists():
        raise FileExistsError(f"Fresh usage log already exists; choose another path: {usage_path}")

    if not tests_path.exists():
        print(f"ERROR: {tests_path} not found. Run extract_unique_tests first.")
        sys.exit(1)

    # Load all tests
    print(f"Loading tests from {tests_path}...")
    tests = []
    with open(tests_path, encoding="utf-8") as f:
        for line in f:
            tests.append(json.loads(line))

    if limit:
        tests = tests[:limit]
        print(f"  Limiting to first {limit} tests")
    print(f"  Loaded {len(tests)} tests to summarize")

    # Check for existing progress (resume support)
    already_done = set()
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                already_done.add(rec["test_path"])
        print(f"  Found {len(already_done)} already-summarized tests (resuming)")

    remaining = [t for t in tests if t["test_path"] not in already_done]
    print(f"  {len(remaining)} tests remaining to summarize")

    if not remaining:
        print("All tests already summarized! Nothing to do.")
        return

    # Setup Gemini
    client = setup_gemini()
    delay = 60.0 / GEMINI_RPM  # seconds between requests

    total_input_tokens = 0
    total_output_tokens = 0
    total_thinking_tokens = 0
    total_written = len(already_done)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as out_f, \
            open(usage_path, "a", encoding="utf-8") as usage_f:
        for i, test in enumerate(remaining):
            test_path = test["test_path"]
            source = test["source_code"]
            # Truncate very long files
            if len(source) > 6000:
                source = source[:6000] + "\n... [truncated]"

            prompt = SUMMARIZE_PROMPT.format(
                test_path=test_path,
                source_code=source,
            )

            print(f"  [{i+1}/{len(remaining)}] {test_path.split('/')[-1]}", end=" ", flush=True)

            # Call Gemini with retry
            response = None
            for attempt in range(5):
                try:
                    response = client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=prompt,
                        config={
                            "max_output_tokens": GEMINI_MAX_OUTPUT_TOKENS,
                            "temperature": 0.2,
                            "thinking_config": {"thinking_budget": 0},  # DISABLE thinking to avoid huge costs
                        },
                    )
                    break
                except Exception as e:
                    error_str = str(e)
                    if "429" in error_str or "quota" in error_str.lower() or "rate" in error_str.lower() or "resource" in error_str.lower():
                        wait_time = 30 * (attempt + 1)
                        print(f"\n    Rate limited, waiting {wait_time}s...", end=" ", flush=True)
                        time.sleep(wait_time)
                    elif "503" in error_str or "unavailable" in error_str.lower():
                        wait_time = 15 * (attempt + 1)
                        print(f"\n    503 overloaded, waiting {wait_time}s...", end=" ", flush=True)
                        time.sleep(wait_time)
                    else:
                        print(f"\n    Error: {error_str[:100]}", end=" ", flush=True)
                        if attempt == 4:
                            print("SKIPPED")
                        time.sleep(10)

            if response is not None:
                usage = getattr(response, "usage_metadata", None)
                prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
                output_tokens = getattr(usage, "candidates_token_count", 0) or 0
                thinking_tokens = getattr(usage, "thoughts_token_count", 0) or 0
                total_input_tokens += prompt_tokens
                total_output_tokens += output_tokens
                total_thinking_tokens += thinking_tokens
                usage_f.write(json.dumps({
                    "test_path": test_path,
                    "model": GEMINI_MODEL,
                    "prompt_tokens": prompt_tokens,
                    "output_tokens": output_tokens,
                    "thinking_tokens": thinking_tokens,
                }) + "\n")
                usage_f.flush()

            if response is None or not response.text:
                print("SKIPPED (no response)")
                continue

            summary = response.text.strip()

            record = {
                "test_path": test_path,
                "summary": summary,
            }
            out_f.write(json.dumps(record) + "\n")
            out_f.flush()
            total_written += 1

            print(f"OK ({len(summary)} chars)")

            # Rate limiting
            time.sleep(delay)

    print(f"\nDone. {total_written} summaries written to {out_path}")
    print(f"Token usage: ~{total_input_tokens} input, ~{total_output_tokens} output, ~{total_thinking_tokens} thinking")
    print(f"Per-response token log: {usage_path}")
    print("Use the provider's billing records for the actual amount charged.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("project", choices=list(PROJECTS.keys()), help="Project key")
    parser.add_argument("--limit", type=int, default=None, help="Only summarize first N tests")
    parser.add_argument("--output", help="Summary JSONL output path")
    parser.add_argument("--usage-log", help="Per-response token usage JSONL path")
    parser.add_argument("--fresh", action="store_true", help="Start a new run; refuse to overwrite output files")
    args = parser.parse_args()
    summarize_tests(args.project, args.limit, args.output, args.usage_log, args.fresh)
