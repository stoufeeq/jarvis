"""
SMTP email service.

Uses Python standard-library smtplib so no extra packages are needed.
Runs the blocking SMTP call in a thread to avoid blocking the async event loop.

Configuration (all via .env):
  SMTP_HOST      — e.g. smtp.gmail.com  (leave blank to disable)
  SMTP_PORT      — 587 (STARTTLS) or 465 (SSL)
  SMTP_USER      — your login / sender address
  SMTP_PASSWORD  — password or App Password
  SMTP_USE_TLS   — true (STARTTLS on 587) / false (plain / SSL on 465)
  ALERT_FROM_EMAIL — display from address

Gmail quick-start:
  1. Enable 2-Step Verification on your Google account
  2. Go to Google Account → Security → App Passwords → generate one
  3. Use smtp.gmail.com / 587 / your Gmail address / the App Password
"""

import asyncio
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import certifi

from app.config import get_settings

log = logging.getLogger(__name__)


def _send_sync(to_address: str, subject: str, html_body: str, text_body: str) -> None:
    """Blocking SMTP send — call via asyncio.to_thread."""
    settings = get_settings()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.alert_from_email
    msg["To"] = to_address
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    # Use certifi's CA bundle — fixes macOS Python SSL cert verification
    context = ssl.create_default_context(cafile=certifi.where())

    if settings.smtp_port == 465:
        # SSL from the start
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context) as server:
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.alert_from_email, to_address, msg.as_string())
    else:
        # STARTTLS (port 587 default)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.ehlo()
            if settings.smtp_use_tls:
                server.starttls(context=context)
                server.ehlo()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.alert_from_email, to_address, msg.as_string())


async def send_email(to_address: str, subject: str, html_body: str, text_body: str = "") -> bool:
    """Send an email asynchronously. Returns True on success, False on failure."""
    settings = get_settings()
    if not settings.email_configured:
        log.debug("Email not configured — skipping send to %s", to_address)
        return False
    if not text_body:
        # Minimal plain-text fallback
        import re
        text_body = re.sub(r"<[^>]+>", "", html_body)
    try:
        await asyncio.to_thread(_send_sync, to_address, subject, html_body, text_body)
        log.info("Email sent to %s: %s", to_address, subject)
        return True
    except Exception as exc:
        log.error("Failed to send email to %s: %s", to_address, exc)
        return False


# ── Pre-built alert email templates ──────────────────────────────────────────

def alert_triggered_email(
    ticker: str,
    alert_type: str,
    threshold: float | None,
    triggered_price: float | None,
) -> tuple[str, str]:
    """Return (subject, html_body) for a triggered price alert."""
    direction = "above" if alert_type == "price_above" else "below"
    threshold_str = f"${threshold:,.2f}" if threshold else "—"
    price_str = f"${triggered_price:,.4f}" if triggered_price else "—"

    subject = f"Jarvis Alert: {ticker} {direction} {threshold_str}"
    html = f"""
<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;background:#0f172a;color:#f1f5f9;padding:32px;">
  <div style="max-width:480px;margin:auto;background:#1e293b;border-radius:12px;
              padding:28px;border:1px solid #334155;">
    <h2 style="margin:0 0 4px;color:#f1f5f9;">🔔 Price Alert Triggered</h2>
    <p style="color:#94a3b8;margin:0 0 24px;font-size:14px;">Jarvis Financial Intelligence</p>

    <table style="width:100%;border-collapse:collapse;font-size:15px;">
      <tr>
        <td style="padding:8px 0;color:#94a3b8;">Ticker</td>
        <td style="padding:8px 0;font-weight:bold;text-align:right;">{ticker}</td>
      </tr>
      <tr>
        <td style="padding:8px 0;color:#94a3b8;">Condition</td>
        <td style="padding:8px 0;text-align:right;">Price {direction} {threshold_str}</td>
      </tr>
      <tr>
        <td style="padding:8px 0;color:#94a3b8;">Current price</td>
        <td style="padding:8px 0;font-weight:bold;color:#f59e0b;text-align:right;">{price_str}</td>
      </tr>
    </table>

    <p style="margin:24px 0 0;font-size:13px;color:#64748b;">
      This alert will not fire again until you re-arm it in the Jarvis app.
    </p>
  </div>
</body>
</html>
"""
    return subject, html


def password_reset_email(code: str, reset_url: str, ttl_minutes: int) -> tuple[str, str]:
    """Return (subject, html_body) for a password reset email.

    The email shows the 6-digit code prominently (the page asks for the
    code, not for clicking a tokenised link) and includes a link to the
    reset page so the recipient can jump straight there.
    """
    subject = "Reset your Jarvis password"
    html = f"""
<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;background:#0f172a;color:#f1f5f9;padding:32px;">
  <div style="max-width:480px;margin:auto;background:#1e293b;border-radius:12px;
              padding:28px;border:1px solid #334155;">
    <h2 style="margin:0 0 4px;color:#f1f5f9;">Reset your password</h2>
    <p style="color:#94a3b8;margin:0 0 24px;font-size:14px;">Jarvis Financial Intelligence</p>

    <p style="font-size:15px;color:#cbd5e1;margin:0 0 16px;">
      Use this 6-digit code to set a new password:
    </p>

    <div style="font-family:'Menlo','Monaco',monospace;font-size:36px;letter-spacing:0.5em;
                background:#0f172a;border:1px solid #334155;border-radius:8px;
                padding:18px 12px;text-align:center;font-weight:bold;color:#f59e0b;
                margin:0 0 24px;">
      {code}
    </div>

    <p style="font-size:14px;color:#cbd5e1;margin:0 0 8px;">
      <a href="{reset_url}" style="color:#60a5fa;text-decoration:none;">
        Open the reset page →
      </a>
    </p>
    <p style="font-size:13px;color:#94a3b8;margin:0 0 24px;">
      Or copy this URL: <span style="color:#94a3b8;">{reset_url}</span>
    </p>

    <p style="margin:0;font-size:13px;color:#64748b;">
      The code expires in {ttl_minutes} minutes and can be used once.
      If you didn't request this, ignore this email — your password stays the same.
    </p>
  </div>
</body>
</html>
"""
    return subject, html
