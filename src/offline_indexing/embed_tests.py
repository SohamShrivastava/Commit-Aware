"""
Step 3: Generate vector embeddings for test summaries using Gemini Embedding API.

Reads test_summaries.jsonl, sends summaries to Gemini Embedding 2 in batches,
and saves embeddings as a numpy file. Also outputs test_index.jsonl
(mapping from index position to test path + summary).

Usage:
    python -m src.offline_indexing.embed_tests log4j2
    python -m src.offline_indexing.embed_tests shardingsphere
"""
import json
import argparse
import sys
import time
import warnings
# pyrefly: ignore [missing-import]
import numpy as np
from pathlib import Path

warnings.filterwarnings("ignore", message=".*automatic function calling.*")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.config import PROJECTS, EMBEDDING_MODEL, GEMINI_API_KEY

from google import genai


def embed_tests(project_key):
    proj = PROJECTS[project_key]
    project_folder = proj["folder"]
    summaries_path = project_folder / "test_summaries.jsonl"
    embeddings_path = project_folder / "test_embeddings.npz"
    index_path = project_folder / "test_index.jsonl"

    if not summaries_path.exists():
        print(f"ERROR: {summaries_path} not found. Run llm_test_summarizer first.")
        sys.exit(1)

    # Setup Gemini client
    if not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY not set in .env")
        sys.exit(1)
    client = genai.Client(api_key=GEMINI_API_KEY)

    # Load summaries
    print(f"Loading summaries from {summaries_path}...")
    test_entries = []
    with open(summaries_path, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("summary"):
                test_entries.append(rec)

    print(f"  Loaded {len(test_entries)} test summaries")

    if not test_entries:
        print("No summaries found. Nothing to embed.")
        return

    # Generate embeddings in batches (Gemini supports batched embed_content)
    print(f"Generating embeddings using {EMBEDDING_MODEL}...")
    BATCH_SIZE = 100  # Gemini supports up to 100 texts per request
    all_embeddings = []
    total_tokens = 0

    for i in range(0, len(test_entries), BATCH_SIZE):
        batch = test_entries[i:i + BATCH_SIZE]
        batch_texts = [t["summary"] for t in batch]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(test_entries) + BATCH_SIZE - 1) // BATCH_SIZE

        # Retry logic
        for attempt in range(3):
            try:
                result = client.models.embed_content(
                    model=EMBEDDING_MODEL,
                    contents=batch_texts,
                )
                break
            except Exception as e:
                if attempt < 2:
                    print(f"    Error, retrying in 10s: {str(e)[:100]}")
                    time.sleep(10)
                else:
                    raise

        batch_embeddings = [emb.values for emb in result.embeddings]
        all_embeddings.extend(batch_embeddings)

        print(f"  Batch {batch_num}/{total_batches}: {len(batch)} embeddings ({len(batch_embeddings[0])} dims)")

        # Small delay to respect rate limits
        if i + BATCH_SIZE < len(test_entries):
            time.sleep(0.5)

    embeddings = np.array(all_embeddings, dtype=np.float32)

    # Save embeddings
    np.savez_compressed(str(embeddings_path), embeddings=embeddings)
    print(f"\n  Saved embeddings ({embeddings.shape}) to {embeddings_path}")

    # Save index (mapping from position to test info)
    with open(index_path, "w", encoding="utf-8") as f:
        for i, entry in enumerate(test_entries):
            record = {
                "index": i,
                "test_path": entry["test_path"],
                "summary": entry["summary"],
            }
            f.write(json.dumps(record) + "\n")

    print(f"  Saved index ({len(test_entries)} entries) to {index_path}")

    # Quick sanity check: find most similar pairs
    # pyrefly: ignore [import-unresolved, missing-import]
    from numpy.linalg import norm
    print(f"\n--- Sanity Check: Top 5 most similar test pairs ---")
    norms = embeddings / (norm(embeddings, axis=1, keepdims=True) + 1e-9)
    sim_matrix = norms @ norms.T
    np.fill_diagonal(sim_matrix, 0)

    flat_indices = np.argsort(sim_matrix.ravel())[-5:][::-1]
    for flat_idx in flat_indices:
        i, j = divmod(flat_idx, sim_matrix.shape[1])
        sim = sim_matrix[i, j]
        name_i = test_entries[i]["test_path"].split("/")[-1]
        name_j = test_entries[j]["test_path"].split("/")[-1]
        print(f"  {name_i} <-> {name_j}: similarity={sim:.4f}")

    # Cost estimate
    est_tokens = sum(len(t["summary"].split()) * 1.3 for t in test_entries)
    print(f"\nEstimated token usage: ~{int(est_tokens)} tokens")
    print(f"Estimated cost: ${est_tokens * 0.006 / 1000:.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("project", choices=list(PROJECTS.keys()), help="Project key")
    args = parser.parse_args()
    embed_tests(args.project)
