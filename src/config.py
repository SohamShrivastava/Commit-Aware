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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-3.5-flash"

# Paid tier — much higher limits than free
GEMINI_RPM = 60            # paid tier allows 1000+ RPM, using 60 to be safe
GEMINI_BATCH_SIZE = 1      # 1 test per request = reliable plain-text responses
GEMINI_MAX_OUTPUT_TOKENS = 512

# ── Embedding model ──
EMBEDDING_MODEL = "gemini-embedding-2"  # 3072 dims, via API, cost tracked for paper

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
