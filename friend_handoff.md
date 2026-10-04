# Project Handoff for Our Friend

**Date:** 4 October 2026

This note explains what has changed in the project, which files are now the clean versions, and what to do next. The aim is for both of us to use the same data and the same code.

## The project in one paragraph

We are trying to save CI time by choosing tests that are relevant to a code change instead of always running every test. For each past CI build, TCP-CI gives us the commit, the tests that ran, their results, and some test features. The Git repository gives us the actual code diff. Later, we will use test summaries and embeddings to compare a code change with tests that may check it.

## What changed

### Build data and code diffs

The old `extracted_dataset.jsonl` files had one entry for each build, but all their test-result lists were empty. The results were present in `exe.csv`; the old output simply had not joined them correctly.

We rebuilt the data and joined each build to its test results. We also fixed how Git diffs are read for merge commits: the code now compares a merge commit with its first parent so the changes are visible. The code no longer scans every file in the repository for every build, which was unnecessary and slow.

The new complete files contain:

| Project | Builds | Test results | Test results with TCP-CI features | Test results without a feature row |
| --- | ---: | ---: | ---: | ---: |
| Log4j | 441 | 240,253 | 117,968 | 122,285 |
| ShardingSphere | 1,049 | 833,541 | 91,042 | 742,499 |

The feature data has 152 columns. A result without a matching feature row is kept with `features: null`; it is not a missing test result.

The complete files are named `complete_dataset.jsonl` and belong in their matching project folders. They contain the build, commit, diff, test results, and available TCP-CI features.

### Test-file list

The old code guessed that something was a test when the word `test` appeared anywhere in its path. That accidentally included ordinary source files. The updated code reads TCP-CI's `EntityType` label and keeps files marked `TEST`.

The clean test counts are:

| Project | Old list | Clean list | Why the count changed |
| --- | ---: | ---: | --- |
| Log4j | 1,068 | 1,064 | Four old entries were not labeled as tests by TCP-CI. |
| ShardingSphere | 3,072 | 2,161 | Many old entries were source files or had no test label; 19 labeled test files could not be read from Git at the selected commit. |

The 19 unavailable ShardingSphere source files are left out of the clean list. The other 2,161 tests are available. We did not regenerate their summaries.

The extractor also uses `git cat-file` to read old source files. On Windows, `git show commit:path` failed for some long paths even when the commit and file were present.

### Summaries and index

The saved summaries were filtered to match the clean test list. No summary API calls were made. The clean files are:

| Project | `unique_tests.jsonl` | `test_summaries.jsonl` | `test_index.jsonl` |
| --- | ---: | ---: | ---: |
| Log4j | 1,064 | 1,064 | 1,064 |
| ShardingSphere | 2,161 | 2,161 | 2,161 |

Each index number points to the test summary at the same position. We checked that the numbers run from zero in order and that the paths match.

### Embedding code

An embedding is a list of numbers made from one test summary. It must have one vector per summary, in the same order.

The old embedding files we checked contained only 11 vectors for Log4j and 31 for ShardingSphere, each with 3,072 numbers. These are not enough for the clean summary lists. We have deleted those old incomplete files from our copy. We did **not** call the embedding API or generate new vectors.

The updated `embed_tests.py` now:

- Uses `unique_tests.jsonl` to ignore summaries for files that are not in the clean test list.
- Stops if any clean test is missing a summary.
- Checks that each API batch returns one vector per summary and that the final total is complete.
- Writes `test_embeddings.npz` and a matching `test_index.jsonl`.

For the clean lists, the expected vector shapes are **1,064 by 3,072** for Log4j and **2,161 by 3,072** for ShardingSphere. Please check any existing embedding files before deciding whether they can be reused. The vector count and test order both need to match. If they do not, run the updated embedding script; that makes embedding API calls, but it does not summarize the tests again.

### Empty diffs

Before the fix, 19 Log4j diffs and 14 ShardingSphere diffs were empty. Most were merge commits; comparing those commits with their first parent revealed their changes. The final data has no empty Log4j diffs and only two empty ShardingSphere diffs: `b7ab87321702` and `d31e051c2bb5`. Git shows that these two have no code changes. They are kept in the data, but should not be treated as normal code-change examples.

### Joining feature data

The new `join_dataset_features.py` joins `dataset.csv` to each test result using the build ID and test ID. It adds the 152 TCP-CI feature values when they exist, and keeps results without a matching row with `features: null`. It uses a temporary local database for the join so it does not need to hold all feature rows in memory.

### Requirements and API key

`requirements.txt` now lists `google-genai`, which matches the `google.genai` import used by the code. Keep the Gemini API key in a local `.env` file. Never put the key in GitHub.

## Files to share

The `TCP-CI-dataset-v2` folder is ignored by Git because it contains large dataset files. That means `git pull` will **not** send the generated JSONL files or embeddings.

Share these files separately, for example through OneDrive, and put each one in the matching project folder:

- `complete_dataset.jsonl`
- `unique_tests.jsonl`
- `test_summaries.jsonl`
- `test_index.jsonl`

The complete datasets are large: about 748 MB for Log4j and 824 MB for ShardingSphere. Do not try to add them to GitHub. The clean test, summary, and index files are smaller, but are also ignored by Git.

If you already have the older suffixed summary files, keep a copy until the clean `test_summaries.jsonl` is in place. The updated embedding script filters those summaries using `unique_tests.jsonl`, so it can reuse summaries for valid tests and ignore the extras.

## Code files to push

Push these source changes so our scripts work the same way:

- `extract_commit_data.py`
- `join_dataset_features.py`
- `src/offline_indexing/extract_unique_tests.py`
- `src/offline_indexing/embed_tests.py`
- `requirements.txt`

Git also shows deletions for old generated files under `files/`. Those are old summaries, indexes, unique-test lists, and incomplete embedding files; keeping those deletions is part of cleaning up the old artifacts.

## Next three steps

1. **Get on the same files.** Pull the code changes. Then copy the four clean JSONL files listed above into the matching local project folders. If the raw TCP-CI dataset or Git repositories are missing on your computer, those must also be available locally.
2. **Check embeddings.** For each project, check that the embedding array has exactly 1,064 or 2,161 rows, has 3,072 numbers per row, and matches `test_index.jsonl` in order. If not, run `python -m src.offline_indexing.embed_tests log4j2` and `python -m src.offline_indexing.embed_tests shardingsphere` with the local API key configured. These commands generate embeddings, not test summaries.
3. **Start the experiment.** Agree on the chronological data split before tuning anything: early builds for setup, middle builds for calibration, and later builds for final testing. Then first compare a simple TCP-CI feature-based selector with running every test; add embedding-based selection after the vectors are verified.
