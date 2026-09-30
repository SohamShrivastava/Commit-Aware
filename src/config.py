"""
Central configuration for the LLM test selection pipeline.

All paths, API settings, and thresholds in one place.
"""
import os
from pathlib import Path

# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

# ── Project root ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # .../ICSE/

# Load .env file from project root
load_dotenv(PROJECT_ROOT / ".env")

# ── Dataset paths ──
DATASETS_DIR = PROJECT_ROOT / "TCP-CI-dataset-v2" / "datasets"

PROJECTS = {
    "log4j2": {
        "folder": DATASETS_DIR / "apache@logging-log4j2",
        "repo_subfolder": "logging-log4j2",
    },
    "shardingsphere": {
        "folder": DATASETS_DIR / "apache@shardingsphere",
        "repo_subfolder": "shardingsphere",
    },
}

# ── Gemini API ──
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-3.8-flash"

# Free-tier limits — adjust RPM based on your model's free tier
GEMINI_RPM = 4            # safe for free tier (most models allow 5 RPM)
GEMINI_BATCH_SIZE = 1     # 1 test per request = much better JSON reliability
GEMINI_MAX_OUTPUT_TOKENS = 512   # single test needs less output

# ── Embedding model ──
EMBEDDING_MODEL = "all-MiniLM-L6-v2"  # sentence-transformers, runs locally, free

# ── Summarization prompt (single test — no JSON array needed) ──
SUMMARIZE_PROMPT = """Analyze this Java test file from a CI/CD pipeline.
Write a concise 2-3 sentence summary describing:
1. What functionality/component this test validates
2. What types of scenarios it covers
3. What source files/modules it likely depends on

Test file: {test_path}
```java
{source_code}
```

Respond with ONLY the summary text, no JSON, no markdown, no extra formatting.
"""
