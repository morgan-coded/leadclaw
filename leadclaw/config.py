"""
leadclaw/config.py - Shared constants and configuration
"""

import os

# Load .env if present
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# AI model — override with LEADCLAW_MODEL env var
# Default: claude-haiku-4-5-20251001 (fast, cheap, good for short drafts)
# Update this default when a newer model is preferred, or set LEADCLAW_MODEL in .env
MODEL = os.getenv("LEADCLAW_MODEL", "claude-haiku-4-5-20251001")

# Shared status display labels
STATUS_LABELS = {
    "new": "🆕 New",
    "quoted": "💬 Quoted",
    "followup_due": "🔔 Follow-up Due",
    "won": "✅ Won",
    "lost": "❌ Lost",
}

# Valid loss reasons
LOST_REASONS = [
    "price",
    "timing",
    "went_competitor",
    "no_response",
    "not_qualified",
    "service_area",
    "other",
]

DEFAULT_FOLLOWUP_DAYS = 3
MAX_FIELD_LENGTH = 500
MAX_NAME_LENGTH = 100
MAX_LIST_ROWS = 200
