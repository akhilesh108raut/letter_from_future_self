"""
Email delivery for Letter From Your Future Self.

Uses stdlib smtplib — no extra dependency. Configure via env vars:
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASS
  EMAIL_FROM   (e.g. "Letter From Your Future Self <you@yourdomain.com>")
  PUBLIC_BASE_URL (e.g. "https://letter-from-future-self.onrender.com")

Works with any SMTP provider (Gmail app password, Zoho, Brevo, Mailgun SMTP,
etc.). If SMTP isn't configured, send_letter_email() is a safe no-op that
logs and returns False — letter generation is never blocked by email.
"""
import os
import smtplib
import logging
from email.message import EmailMessage
from email.utils import formataddr

log = logging.getLogger("letter.email")


def _smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASS"))


def _base_url() -> str:
    return (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")


def send_letter_email(to_email: str, name: str, letter_uuid: str, ai: dict) -> bool:
    """Email the reader a link to their letter (and a short preview). Returns
    True if sent, False if skipped/failed — never raises to the caller."""
    if not to_email:
        return False
    if not _smtp_configured():
        log.info("SMTP not configured — skipping letter email to %s", to_email)
        return False

    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")
    from_addr = os.getenv("EMAIL_FROM") or formataddr(("Your Future Self", user))

    first = (name or "friend").strip().split()[0] if name else "friend"
    link = f"{_base_url()}/letter/read/{letter_uuid}" if _base_url() else f"/letter/read/{letter_uuid}"
    chapter = ai.get("life_chapter", "A Letter From Your Future Self")
    truth = ai.get("core_truth", "")
    # First real paragraph of the letter as a teaser (skip the salutation).
    paras = [p.strip() for p in (ai.get("letter") or "").split("\n\n") if p.strip()]
    teaser = paras[1] if len(paras) > 1 else (paras[0] if paras else "")
    teaser = teaser[:280] + ("…" if len(teaser) > 280 else "")

    msg = EmailMessage()
    msg["Subject"] = "Your letter from your future self ✦"
    msg["From"] = from_addr
    msg["To"] = to_email

    msg.set_content(
        f"Dear {first},\n\n"
        f"Your letter is ready — {chapter}.\n\n"
        f"{('“' + truth + '”') if truth else ''}\n\n"
        f"{teaser}\n\n"
        f"Read the whole thing here (the link is yours to keep):\n{link}\n\n"
        f"— Letter From Your Future Self"
    )
    msg.add_alternative(f"""\
<div style="background:#0b0820;padding:32px 0;font-family:Georgia,serif">
  <div style="max-width:520px;margin:0 auto;background:#f4e8cc;border-radius:8px;padding:40px 34px;color:#2c2413">
    <div style="text-align:center;letter-spacing:.24em;text-transform:uppercase;font-size:11px;color:#8a6d1f;margin-bottom:14px">{chapter}</div>
    {f'<p style="text-align:center;font-style:italic;font-size:18px;color:#6b4f1f;margin:0 0 24px">&ldquo;{truth}&rdquo;</p>' if truth else ''}
    <p style="font-size:16px;line-height:1.8">Dear {first},</p>
    <p style="font-size:15px;line-height:1.9">{teaser}</p>
    <div style="text-align:center;margin:30px 0 6px">
      <a href="{link}" style="display:inline-block;background:linear-gradient(120deg,#f1d488,#d9b25b);color:#1a1206;text-decoration:none;font-weight:bold;padding:14px 30px;border-radius:999px">Read your full letter</a>
    </div>
    <p style="text-align:center;font-size:12px;color:#8a7a55;margin-top:18px">This link is yours to keep — open it anytime.</p>
  </div>
</div>""", subtype="html")

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
        log.info("Letter email sent to %s", to_email)
        return True
    except Exception:
        log.exception("Failed to send letter email to %s", to_email)
        return False
