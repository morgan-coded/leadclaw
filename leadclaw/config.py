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

# Shared status display labels (emoji)
STATUS_LABELS = {
    "new": "🆕 New",
    "quoted": "💬 Quoted",
    "followup_due": "🔔 Follow-up Due",
    "booked": "📅 Booked",
    "completed": "🔧 Completed",
    "paid": "💰 Paid",
    "won": "💰 Paid",
    "lost": "❌ Lost",
}

# Plain-text status labels for --plain / no-emoji output
STATUS_LABELS_PLAIN = {
    "new": "[new]",
    "quoted": "[quoted]",
    "followup_due": "[followup_due]",
    "booked": "[booked]",
    "completed": "[completed]",
    "paid": "[paid]",
    "won": "[paid]",
    "lost": "[lost]",
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

# Default follow-up window for new leads and quotes (days until the next nudge)
DEFAULT_FOLLOWUP_DAYS = 3
# Fallback recurring service interval when not explicitly set and service type is unknown
DEFAULT_RECURRING_DAYS = int(os.getenv("LEADCLAW_RECURRING_DAYS", "90"))
DEFAULT_INVOICE_REMINDER_DAYS = int(os.getenv("LEADCLAW_INVOICE_REMINDER_DAYS", "3"))
MAX_FIELD_LENGTH = 500
MAX_NAME_LENGTH = 100
MAX_LIST_ROWS = 200

# Email verification: set to "0" to auto-verify on signup (for local dev / testing)
REQUIRE_VERIFICATION = os.getenv("LEADCLAW_REQUIRE_VERIFICATION", "1").strip() != "0"
