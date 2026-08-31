from __future__ import annotations

import json
import os
from typing import Any
import httpx


def format_message(deal: dict[str, Any]) -> str:
    a = deal["analysis"]
    return (
        f"🔥 DealHunter {a['deal_score']}/100\n"
        f"{deal['title']}\n"
        f"Vraagprijs: €{deal['asking_price']:.0f}\n"
        f"Waarde: ±€{a['expected_resale']:.0f}\n"
        f"Verwachte winst: €{a['expected_profit']:.0f} ({a['roi_percent']:.0f}% ROI)\n"
        f"Risico: {a['risk_score']}/100 | {a['verdict']}\n"
        f"{deal.get('url') or ''}"
    ).strip()


def send_twilio_whatsapp(deal: dict[str, Any]) -> str:
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    sender = os.environ.get("TWILIO_FROM")
    recipient = os.environ.get("TWILIO_TO")
    content_sid = os.environ.get("TWILIO_CONTENT_SID")
    if not all([sid, token, sender, recipient]):
        raise RuntimeError("Twilio WhatsApp secrets zijn niet compleet")

    sender = sender if sender.startswith("whatsapp:") else f"whatsapp:{sender}"
    recipient = recipient if recipient.startswith("whatsapp:") else f"whatsapp:{recipient}"
    a = deal["analysis"]
    payload: dict[str, str] = {"From": sender, "To": recipient}
    if content_sid:
        payload["ContentSid"] = content_sid
        payload["ContentVariables"] = json.dumps({
            "1": deal["title"][:120],
            "2": f"{deal['asking_price']:.0f}",
            "3": f"{a['expected_resale']:.0f}",
            "4": f"{a['expected_profit']:.0f}",
            "5": str(a["deal_score"]),
            "6": deal.get("url") or "Geen link",
        }, ensure_ascii=False)
    else:
        payload["Body"] = format_message(deal)

    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    with httpx.Client(timeout=25) as client:
        r = client.post(url, data=payload, auth=(sid, token))
        r.raise_for_status()
        data = r.json()
    return str(data.get("sid") or "sent")
