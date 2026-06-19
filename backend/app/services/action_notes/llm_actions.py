"""Template fallbacks for Action Notes email drafts and action boards."""
from __future__ import annotations

from typing import Any


def draft_email(
    user_name: str,
    user_email: str,
    contact: dict,
    notes: list[str],
    pins_summary: list[str],
    language: str = "en",
) -> dict[str, Any]:
    sal = contact.get("salutation_en") or f"Hello {contact.get('display_name', '').strip()},"
    note_block = "\n".join(f"• {n}" for n in notes[:8]) or "• (no notes attached)"
    pin_block = ""
    if pins_summary:
        pin_block = "\n\nPinned evidence links:\n" + "\n".join(f"• {p}" for p in pins_summary[:8])
    body = (
        f"{sal}\n\n"
        f"Following our review of the latest financial data, I would like to align on the points below.\n\n"
        f"Action Notes discussed:\n{note_block}{pin_block}\n\n"
        f"Could we schedule a short follow-up this week?\n\n"
        "Best regards,"
    )
    return {
        "subject": "Follow-up on financial review — action items",
        "body_text": body,
        "body_html": f"<pre style='font-family:sans-serif'>{body}</pre>",
        "action_items": notes[:5],
    }


def generate_board(notes: list[str], contacts: list[dict]) -> dict[str, Any]:
    assignee = contacts[0]["contact_id"] if contacts else None
    items = []
    for i, note in enumerate(notes[:8]):
        items.append({
            "id": f"item_{i + 1}",
            "title": note[:120] or f"Action item {i + 1}",
            "status": "open",
            "priority": "medium",
            "assignee_contact_id": assignee,
            "due_date": None,
        })
    return {
        "title": "Action board",
        "columns": [{"id": "open", "title": "Open", "items": items}],
        "activity_log": [],
    }
