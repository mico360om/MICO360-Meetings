"""Send email via SMTP (configured for Mailjet by default).

Credentials are NEVER hardcoded — they are read from the app settings (which
live in the per-user data folder, not in source control). Mailjet SMTP uses the
API key as the username and the secret key as the password.

Mailjet SMTP: host in-v3.mailjet.com, ports 25 / 587 (STARTTLS) / 465 (SSL).
Port 587 is the most reliable; port 25 is often blocked for outbound by ISPs.
"""
from __future__ import annotations

import logging
import mimetypes
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger("mico360.emailer")

DEFAULT_HOST = "in-v3.mailjet.com"
DEFAULT_PORT = 587


@dataclass
class SmtpConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    user: str = ""            # Mailjet API key
    password: str = ""        # Mailjet Secret key
    sender: str = ""          # validated sender, e.g. admin@mico360.com

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and self.password and self.sender)

    @classmethod
    def from_settings(cls, s) -> "SmtpConfig":
        return cls(
            host=s.get("smtp_host", DEFAULT_HOST) or DEFAULT_HOST,
            port=int(s.get("smtp_port", DEFAULT_PORT) or DEFAULT_PORT),
            user=s.get("smtp_user", ""),
            password=s.get("smtp_password", ""),
            sender=s.get("email_from", ""),
        )


def _connect(cfg: SmtpConfig, timeout: float = 25.0) -> smtplib.SMTP:
    if cfg.port == 465:
        server = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=timeout,
                                  context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(cfg.host, cfg.port, timeout=timeout)
        try:
            server.ehlo()
            # Never send the SMTP login in cleartext: if the server doesn't offer
            # STARTTLS (or it was stripped on the network), refuse to log in.
            if not server.has_extn("starttls"):
                raise smtplib.SMTPNotSupportedError(
                    f"{cfg.host}:{cfg.port} does not offer an encrypted connection "
                    "(STARTTLS) — use port 587 or 465 so your password isn't sent in the clear.")
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        except Exception:
            server.close()
            raise
    try:
        server.login(cfg.user, cfg.password)
    except Exception:
        server.close()
        raise
    return server


def _split_addrs(value) -> list[str]:
    """A string ("a@x.com; b@y.com" or "a@x.com, b@y.com") or a list -> addresses.
    Semicolon-separated lists used to be treated as ONE header, so only the first
    recipient got the email (M24)."""
    from email.utils import formataddr, getaddresses
    items = [value] if isinstance(value, str) else list(value or [])
    pieces: list[str] = []
    for it in items:                       # split on , ; outside "quotes" and <angles>
        buf, quote, angle = [], False, False
        for ch in str(it):
            if ch == '"' and not angle:
                quote = not quote
            elif ch == "<" and not quote:
                angle = True
            elif ch == ">" and not quote:
                angle = False
            if ch in ",;" and not quote and not angle:
                pieces.append("".join(buf)); buf = []
            else:
                buf.append(ch)
        pieces.append("".join(buf))
    out: list[str] = []
    for piece in (p.strip() for p in pieces):
        if not piece:                      # "a@x.com;" — ignore the empty tail
            continue
        for name, addr in getaddresses([piece]):
            if addr.strip():
                out.append(formataddr((name, addr.strip())) if name else addr.strip())
    return out


def send_email(cfg: SmtpConfig, to, subject: str, body: str,
               html: str | None = None, attachments: list[str] | None = None,
               cc=None) -> None:
    """Send an email. `to`/`cc` may be a string or list. Raises on failure."""
    if not cfg.configured:
        raise ValueError("Email is not configured. Add SMTP settings in Settings → Email.")
    to_list = _split_addrs(to)
    to_list = [a.strip() for a in to_list if a.strip()]
    if not to_list:
        raise ValueError("No recipient address provided.")

    msg = EmailMessage()
    msg["From"] = cfg.sender
    msg["To"] = ", ".join(to_list)
    if cc:
        cc_list = _split_addrs(cc)
        msg["Cc"] = ", ".join(a.strip() for a in cc_list if a.strip())
    msg["Subject"] = subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    for path in (attachments or []):
        p = Path(path)
        if not p.exists():
            continue
        ctype, _ = mimetypes.guess_type(str(p))
        maintype, subtype = (ctype.split("/", 1) if ctype else ("application", "octet-stream"))
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)

    server = _connect(cfg)
    try:
        server.send_message(msg)
        log.info("email sent to %s (subject: %s)", to_list, subject)
    finally:
        try:
            server.quit()
        except Exception:
            pass


def send_test(cfg: SmtpConfig, to: str | None = None) -> None:
    """Send a self-test email (to the sender by default) to verify config."""
    recipient = to or cfg.sender
    send_email(cfg, recipient, "MICO360 Meetings - test email",
               "This is a test email from MICO360 Meetings. Your SMTP settings work.")
