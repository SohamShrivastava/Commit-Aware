"""
Step 3: Generate vector embeddings for test summaries.

Reads test_summaries.jsonl, runs each summary through a local sentence-transformer
model, and saves the embeddings as a numpy file. Also outputs test_index.jsonl
(mapping from index position to test path + summary).

Usage:
    python -m src.offline_indexing.embed_tests log4j2
    python -m src.offline_indexing.embed_tests shardingsphere
"""
import json
import argparse
import sys
# pyrefly: ignore [missing-import]
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.config import PROJECTS, EMBEDDING_MODEL

try:
    # pyrefly: ignore [missing-import]
    from sentence_transformers import SentenceTransformer
except ImportError:
    print("ERROR: sentence-transformers not installed. Run:")
    print("  pip install sentence-transformers")
    sys.exit(1)


def embed_tests(project_key):
    proj = PROJECTS[project_key]
    project_folder = proj["folder"]
    summaries_path = project_folder / "test_summaries.jsonl"
    embeddings_path = project_folder / "test_embeddings.npz"
    index_path = project_folder / "test_index.jsonl"

    if not summaries_path.exists():
        print(f"ERROR: {summaries_path} not found. Run llm_test_summarizer first.")
        sys.exit(1)

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

    # Load embedding model
    print(f"Loading embedding model: {EMBEDDING_MODEL}...")
    model = SentenceTransformer(EMBEDDING_MODEL)

    # Generate embeddings
    summaries = [t["summary"] for t in test_entries]
    print(f"Generating embeddings for {len(summaries)} summaries...")
    embeddings = model.encode(summaries, show_progress_bar=True, batch_size=64)

    # Save embeddings
    np.savez_compressed(str(embeddings_path), embeddings=embeddings)
    print(f"  Saved embeddings ({embeddings.shape}) to {embeddings_path}")

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

    # Quick sanity check: find most similar pair
    # pyrefly: ignore [import-unresolved, missing-import]
    from numpy.linalg import norm
    print(f"\n--- Sanity Check: Top 5 most similar test pairs ---")
    norms = embeddings / (norm(embeddings, axis=1, keepdims=True) + 1e-9)
    sim_matrix = norms @ norms.T
    # Zero out diagonal
    np.fill_diagonal(sim_matrix, 0)

    # Get top 5 pairs
    flat_indices = np.argsort(sim_matrix.ravel())[-5:][::-1]
    for flat_idx in flat_indices:
        i, j = divmod(flat_idx, sim_matrix.shape[1])
        sim = sim_matrix[i, j]
        name_i = test_entries[i]["test_path"].split("/")[-1]
        name_j = test_entries[j]["test_path"].split("/")[-1]
        print(f"  {name_i} <-> {name_j}: similarity={sim:.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("project", choices=list(PROJECTS.keys()), help="Project key")
    args = parser.parse_args()
    embed_tests(args.project)
