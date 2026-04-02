"""
Email notification service.

Delivers deal alerts directly to users' registered ``notification_email``
addresses when they have ``email_alerts_enabled = True``.

Uses Python's built-in ``smtplib`` (no additional dependencies) and runs
the blocking SMTP call in a thread executor to stay non-blocking in the
async FastAPI context.

Configuration (.env)
~~~~~~~~~~~~~~~~~~~~~
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USERNAME=you@gmail.com
    SMTP_PASSWORD=app-password
    SMTP_FROM_EMAIL=noreply@flipperai.com
    SMTP_FROM_NAME=Flipper AI
    SMTP_USE_TLS=true

    # Gmail tip: use an App Password (not your main password).
    # For SendGrid: host=smtp.sendgrid.net, port=587, user=apikey, pw=SG.*
    # For Mailgun:  host=smtp.mailgun.org, port=587, user=postmaster@..., pw=...

Supported alert types
~~~~~~~~~~~~~~~~~~~~~
    NEW_MATCH          — a distressed property has been matched to a listing
    PRICE_DROP         — an active listing price has dropped
    SAVED_SEARCH_MATCH — a new deal matches a user's saved search criteria
    STRONG_DEAL        — a match has been flagged as a strong deal (score ≥ 70)
"""
from __future__ import annotations

import asyncio
import email.mime.multipart
import email.mime.text
import logging
import smtplib
from datetime import datetime
from typing import Any, Dict, Optional

from app.models.alert import Alert
from app.models.user import User

logger = logging.getLogger(__name__)

# ---- Email template helpers --------------------------------------------------

def _html_wrapper(title: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<style>
  body {{ font-family: Arial, sans-serif; background: #f5f5f5; color: #222; margin: 0; padding: 0; }}
  .container {{ max-width: 600px; margin: 32px auto; background: #fff; border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,.12); overflow: hidden; }}
  .header {{ background: #1a3c5e; color: #fff; padding: 24px 32px; }}
  .header h1 {{ margin: 0; font-size: 22px; }}
  .body {{ padding: 24px 32px; line-height: 1.6; }}
  .metric {{ display: inline-block; background: #eef3f8; border-radius: 6px;
             padding: 10px 18px; margin: 6px 6px 6px 0; }}
  .metric .label {{ font-size: 11px; color: #666; text-transform: uppercase; }}
  .metric .value {{ font-size: 20px; font-weight: bold; color: #1a3c5e; }}
  .badge {{ display: inline-block; border-radius: 4px; padding: 3px 10px;
            font-weight: bold; font-size: 13px; }}
  .grade-A {{ background: #d4edda; color: #155724; }}
  .grade-B {{ background: #cce5ff; color: #004085; }}
  .grade-C {{ background: #fff3cd; color: #856404; }}
  .grade-D {{ background: #f8d7da; color: #721c24; }}
  .cta {{ display: block; margin: 20px 0; padding: 12px 24px; background: #1a3c5e;
          color: #fff; text-decoration: none; border-radius: 6px;
          text-align: center; font-weight: bold; font-size: 15px; }}
  .footer {{ background: #f0f0f0; color: #888; font-size: 11px;
             padding: 14px 32px; text-align: center; }}
</style>
</head>
<body>
<div class="container">
  <div class="header"><h1>🏠 Flipper AI — {title}</h1></div>
  <div class="body">{body_html}</div>
  <div class="footer">
    You're receiving this because you enabled email alerts on Flipper AI.<br>
    To unsubscribe, disable email alerts in your account settings.
  </div>
</div>
</body>
</html>"""


def _alert_email_content(alert: Alert, analysis: Optional[Dict[str, Any]] = None) -> tuple[str, str]:
    """Return (subject, html_body) for an alert email."""
    atype = alert.alert_type
    details = alert.details or {}

    if atype == "NEW_MATCH":
        subject = "🔔 New Property Match Found"
        score = details.get("deal_score", "N/A")
        body = f"""
        <p><strong>A new distressed property has been matched to an active listing!</strong></p>
        <p>{alert.message}</p>
        """
        if analysis:
            flip = analysis.get("flip_analysis", {})
            rental = analysis.get("rental_analysis", {})
            grade = analysis.get("deal_grade", "?")
            rec = analysis.get("recommendation", "")
            body += f"""
            <p>
              <span class="badge grade-{grade}">Grade {grade}</span>&nbsp;
              <strong>{rec}</strong>
            </p>
            <div>
              <div class="metric">
                <div class="label">Flip ROI</div>
                <div class="value">{flip.get('roi_pct', 0):.1f}%</div>
              </div>
              <div class="metric">
                <div class="label">Net Flip Profit</div>
                <div class="value">${flip.get('net_profit', 0):,.0f}</div>
              </div>
              <div class="metric">
                <div class="label">Cap Rate</div>
                <div class="value">{rental.get('cap_rate_pct', 0):.1f}%</div>
              </div>
              <div class="metric">
                <div class="label">Monthly Cash Flow</div>
                <div class="value">${rental.get('monthly_cash_flow_levered', 0):,.0f}</div>
              </div>
            </div>
            <p><strong>Address:</strong> {analysis.get('property_address', '')}</p>
            """

    elif atype == "PRICE_DROP":
        subject = "📉 Price Drop Alert"
        old = details.get("old_price", 0)
        new = details.get("new_price", 0)
        drop = details.get("drop_pct", 0)
        body = f"""
        <p><strong>A listing you may be tracking has dropped in price!</strong></p>
        <p>{alert.message}</p>
        <div>
          <div class="metric">
            <div class="label">Previous Price</div>
            <div class="value">${old:,.0f}</div>
          </div>
          <div class="metric">
            <div class="label">New Price</div>
            <div class="value">${new:,.0f}</div>
          </div>
          <div class="metric">
            <div class="label">Price Drop</div>
            <div class="value">{drop:.1f}%</div>
          </div>
        </div>
        """

    elif atype == "SAVED_SEARCH_MATCH":
        subject = "🔍 New Deal Matches Your Saved Search"
        search_name = details.get("saved_search_name", "your saved search")
        body = f"""
        <p><strong>A new deal matches <em>{search_name}</em>!</strong></p>
        <p>{alert.message}</p>
        """
        if analysis:
            flip = analysis.get("flip_analysis", {})
            grade = analysis.get("deal_grade", "?")
            rec = analysis.get("recommendation", "")
            body += f"""
            <p>
              <span class="badge grade-{grade}">Grade {grade}</span>&nbsp;
              <strong>{rec}</strong>
            </p>
            <div>
              <div class="metric">
                <div class="label">Flip ROI</div>
                <div class="value">{flip.get('roi_pct', 0):.1f}%</div>
              </div>
              <div class="metric">
                <div class="label">Net Profit</div>
                <div class="value">${flip.get('net_profit', 0):,.0f}</div>
              </div>
            </div>
            <p><strong>Address:</strong> {analysis.get('property_address', '')}</p>
            """

    elif atype == "STRONG_DEAL":
        subject = "🔥 Strong Deal Alert — Act Now!"
        body = f"""
        <p><strong>A high-scoring deal has been flagged for you!</strong></p>
        <p>{alert.message}</p>
        """
        if analysis:
            flip = analysis.get("flip_analysis", {})
            rental = analysis.get("rental_analysis", {})
            grade = analysis.get("deal_grade", "?")
            rec = analysis.get("recommendation", "")
            body += f"""
            <p>
              <span class="badge grade-{grade}">Grade {grade}</span>&nbsp;
              <strong>{rec}</strong>
            </p>
            <div>
              <div class="metric">
                <div class="label">Flip ROI</div>
                <div class="value">{flip.get('roi_pct', 0):.1f}%</div>
              </div>
              <div class="metric">
                <div class="label">Net Profit</div>
                <div class="value">${flip.get('net_profit', 0):,.0f}</div>
              </div>
              <div class="metric">
                <div class="label">Cap Rate</div>
                <div class="value">{rental.get('cap_rate_pct', 0):.1f}%</div>
              </div>
              <div class="metric">
                <div class="label">Cash-on-Cash</div>
                <div class="value">{rental.get('cash_on_cash_return_pct', 0):.1f}%</div>
              </div>
            </div>
            """
    else:
        subject = f"Flipper AI Alert: {atype}"
        body = f"<p>{alert.message}</p>"

    return subject, _html_wrapper(subject, body)


# ---- SMTP sender -------------------------------------------------------------

def _send_email_sync(
    to_email: str,
    subject: str,
    html_body: str,
    smtp_host: str,
    smtp_port: int,
    smtp_username: Optional[str],
    smtp_password: Optional[str],
    from_email: str,
    from_name: str,
    use_tls: bool,
) -> None:
    """Blocking SMTP send — run via executor in async context."""
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg.attach(email.mime.text.MIMEText(html_body, "html", "utf-8"))

    if use_tls:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
        server.ehlo()
        server.starttls()
    else:
        server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15)

    try:
        if smtp_username and smtp_password:
            server.login(smtp_username, smtp_password)
        server.sendmail(from_email, [to_email], msg.as_string())
    finally:
        server.quit()


# ---- Public async API --------------------------------------------------------

class NotificationService:
    """
    Async email notification service.

    Parameters are loaded from ``app.config.Settings`` by the convenience
    wrapper functions below.  Pass ``smtp_host=None`` to disable delivery
    (alerts are still stored in the DB).
    """

    def __init__(
        self,
        smtp_host: Optional[str],
        smtp_port: int = 587,
        smtp_username: Optional[str] = None,
        smtp_password: Optional[str] = None,
        from_email: str = "noreply@flipperai.com",
        from_name: str = "Flipper AI",
        use_tls: bool = True,
    ) -> None:
        self._host = smtp_host
        self._port = smtp_port
        self._user = smtp_username
        self._pass = smtp_password
        self._from = from_email
        self._name = from_name
        self._tls = use_tls

    @property
    def enabled(self) -> bool:
        return bool(self._host)

    async def send_alert(
        self,
        user: User,
        alert: Alert,
        analysis: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Send an email for ``alert`` to ``user.notification_email``.

        Returns True on success, False if delivery was skipped or failed.
        """
        if not self.enabled:
            return False
        if not user.email_alerts_enabled:
            return False
        to_email = user.notification_email or user.email
        if not to_email:
            return False

        subject, html_body = _alert_email_content(alert, analysis)

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: _send_email_sync(
                    to_email=to_email,
                    subject=subject,
                    html_body=html_body,
                    smtp_host=self._host,
                    smtp_port=self._port,
                    smtp_username=self._user,
                    smtp_password=self._pass,
                    from_email=self._from,
                    from_name=self._name,
                    use_tls=self._tls,
                ),
            )
            logger.info(
                "[NOTIFY] Email '%s' sent to %s",
                alert.alert_type,
                to_email,
            )
            return True
        except Exception as exc:
            logger.warning(
                "[NOTIFY] Failed to send email to %s: %s",
                to_email,
                exc,
            )
            return False

    async def send_bulk_unread_alerts(
        self,
        user: User,
        alerts: list[Alert],
        analyses: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> int:
        """Send individual emails for each unread alert. Returns sent count."""
        sent = 0
        for alert in alerts:
            analysis = (analyses or {}).get(str(alert.match_id)) if alert.match_id else None
            ok = await self.send_alert(user, alert, analysis)
            if ok:
                sent += 1
        return sent


# ---- Convenience factory -------------------------------------------------------

def build_notification_service() -> NotificationService:
    """Build a NotificationService from application settings."""
    from app.config import settings
    return NotificationService(
        smtp_host=settings.SMTP_HOST,
        smtp_port=settings.SMTP_PORT,
        smtp_username=settings.SMTP_USERNAME,
        smtp_password=settings.SMTP_PASSWORD,
        from_email=settings.SMTP_FROM_EMAIL,
        from_name=settings.SMTP_FROM_NAME,
        use_tls=settings.SMTP_USE_TLS,
    )
