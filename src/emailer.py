"""Send the brief through Gmail SMTP (needs a Gmail App Password, not your normal password)."""
from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send(subject: str, html: str, text: str) -> None:
    user = os.environ["EMAIL_USER"]
    password = os.environ["EMAIL_APP_PASSWORD"]
    to = [x.strip() for x in os.getenv("EMAIL_TO", user).split(",") if x.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(to)
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port, timeout=30) as s:
        s.login(user, password)
        s.sendmail(user, to, msg.as_string())
