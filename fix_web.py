with open('leadclaw/web.py', 'r', encoding='utf-8') as f:
    content = f.read()

changes = []

# 1. Update imports
old = """from leadclaw.queries import (
    add_lead,
    delete_lead,
    get_all_active_leads,
    get_all_leads,
    get_lead_by_id,
    get_pipeline_summary,
    get_stale_leads,
    get_today_leads,
    mark_lost,
    mark_won,
    update_lead,
    update_quote,
)"""
new = """from leadclaw.queries import (
    add_lead,
    delete_lead,
    get_all_active_leads,
    get_all_leads,
    get_invoice_reminders,
    get_lead_by_id,
    get_pipeline_summary,
    get_service_reminders,
    get_stale_leads,
    get_today_leads,
    mark_booked,
    mark_completed,
    mark_invoice_sent,
    mark_lost,
    mark_paid,
    mark_won,
    set_next_service,
    update_lead,
    update_quote,
)"""
assert old in content, "imports not found"; content = content.replace(old, new); changes.append("imports")

# 2. _lead_to_dict
old = '''def _lead_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "service": row["service"],
        "status": row["status"],
        "phone": row["phone"],
        "email": row["email"],
        "quote_amount": row["quote_amount"],
        "follow_up_after": str(row["follow_up_after"])[:10] if row["follow_up_after"] else None,
        "notes": row["notes"],
        "lost_reason": row["lost_reason"],
        "lost_reason_notes": row["lost_reason_notes"]
        if "lost_reason_notes" in row.keys()
        else None,
    }'''
new = '''def _lead_to_dict(row) -> dict:
    def _safe_col(key, default=None):
        try:
            return row[key]
        except (IndexError, KeyError):
            return default

    return {
        "id": row["id"],
        "name": row["name"],
        "service": row["service"],
        "status": row["status"],
        "phone": row["phone"],
        "email": row["email"],
        "quote_amount": row["quote_amount"],
        "follow_up_after": str(row["follow_up_after"])[:10] if row["follow_up_after"] else None,
        "notes": row["notes"],
        "lost_reason": row["lost_reason"],
        "lost_reason_notes": _safe_col("lost_reason_notes"),
        "scheduled_date": str(_safe_col("scheduled_date"))[:10] if _safe_col("scheduled_date") else None,
        "booked_at": str(_safe_col("booked_at"))[:10] if _safe_col("booked_at") else None,
        "completed_at": str(_safe_col("completed_at"))[:10] if _safe_col("completed_at") else None,
        "invoice_amount": _safe_col("invoice_amount"),
        "invoice_sent_at": str(_safe_col("invoice_sent_at"))[:10] if _safe_col("invoice_sent_at") else None,
        "paid_at": str(_safe_col("paid_at"))[:10] if _safe_col("paid_at") else None,
        "next_service_due_at": str(_safe_col("next_service_due_at"))[:10] if _safe_col("next_service_due_at") else None,
        "invoice_reminder_at": str(_safe_col("invoice_reminder_at"))[:10] if _safe_col("invoice_reminder_at") else None,
        "service_reminder_at": str(_safe_col("service_reminder_at"))[:10] if _safe_col("service_reminder_at") else None,
    }'''
assert old in content, "_lead_to_dict not found"; content = content.replace(old, new); changes.append("_lead_to_dict")

# 3. api_summary return
old = '''    return {
        "pipeline": {
            "open_value": totals["open_value"],
            "won_value": totals["won_value"],
            "lost_value": totals["lost_value"],
            "by_status": by_status,
        },
        "today": today,
        "stale": stale,
        "active": active,
    }'''
new = '''    return {
        "pipeline": {
            "open_value": totals["open_value"],
            "won_value": totals["won_value"],
            "lost_value": totals["lost_value"],
            "by_status": by_status,
        },
        "today": today,
        "stale": stale,
        "active": active,
        "invoice_reminders": [_lead_to_dict(r) for r in get_invoice_reminders(user_id=user_id)],
        "service_reminders": [_lead_to_dict(r) for r in get_service_reminders(user_id=user_id)],
    }'''
assert old in content, "api_summary return not found"; content = content.replace(old, new); changes.append("api_summary")

# 4. CSS badges
old = "  .badge-won{background:#0d3321;color:#22c55e;}\n  .badge-lost{background:#3b0d0d;color:#ef4444;}"
new = "  .badge-won{background:#0d3321;color:#22c55e;}\n  .badge-lost{background:#3b0d0d;color:#ef4444;}\n  .badge-booked{background:#1a3a1a;color:#4ade80;}\n  .badge-completed{background:#1a2a3a;color:#60a5fa;}\n  .badge-paid{background:#1a3a2a;color:#34d399;}"
assert old in content, "CSS badges not found"; content = content.replace(old, new); changes.append("CSS badges")

# 5. Tabs
old = "    <div class=\"tab active\" onclick=\"switchTab('pipeline')\">Pipeline</div>\n    <div class=\"tab\" onclick=\"switchTab('closed')\">Closed</div>\n    <div class=\"tab\" id=\"tab-btn-pilot\" onclick=\"switchTab('pilot')\">Pilot</div>"
new = "    <div class=\"tab active\" onclick=\"switchTab('pipeline')\">Pipeline</div>\n    <div class=\"tab\" onclick=\"switchTab('closed')\">Closed</div>\n    <div class=\"tab\" onclick=\"switchTab('reminders')\">Reminders</div>\n    <div class=\"tab\" id=\"tab-btn-pilot\" onclick=\"switchTab('pilot')\">Pilot</div>"
assert old in content, "tabs not found"; content = content.replace(old, new); changes.append("tabs")

# 6. Reminders panel
old = "  <div class=\"tab-panel\" id=\"tab-pilot\">"
new = "  <div class=\"tab-panel\" id=\"tab-reminders\">\n    <section><h2>Invoice Reminders</h2><div class=\"lead-list\" id=\"invoice-reminders\"></div></section>\n    <section><h2>Recurring Service Due</h2><div class=\"lead-list\" id=\"service-reminders\"></div></section>\n  </div>\n\n  <div class=\"tab-panel\" id=\"tab-pilot\">"
assert old in content, "pilot panel not found"; content = content.replace(old, new, 1); changes.append("reminders panel")

# 7. switchTab
old = "function switchTab(name){\n  document.querySelectorAll('.tab').forEach((t,i)=>t.classList.toggle('active',['pipeline','closed','pilot'][i]===name));"
new = "function switchTab(name){\n  document.querySelectorAll('.tab').forEach((t,i)=>t.classList.toggle('active',['pipeline','closed','reminders','pilot'][i]===name));"
assert old in content, "switchTab not found"; content = content.replace(old, new); changes.append("switchTab")

print("Changes so far:", changes)

with open('leadclaw/web.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Part 1 written OK")
