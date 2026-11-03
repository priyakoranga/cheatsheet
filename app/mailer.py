import logging
import smtplib
import ssl
from email.message import EmailMessage

from itsdangerous import URLSafeTimedSerializer

from . import config

log = logging.getLogger("mailer")
_ser = URLSafeTimedSerializer(config.SECRET_KEY, salt="email-verify")


def make_token(user_id: int, email: str) -> str:
    return _ser.dumps({"uid": user_id, "e": email})


def read_token(token: str, max_age: int = 2 * 24 * 3600) -> dict:
    return _ser.loads(token, max_age=max_age)  # raises BadSignature / SignatureExpired


def send_mail(to: str, subject: str, body: str) -> bool:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.SMTP_FROM, to, subject
    msg.set_content(body)
    try:
        if config.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(config.SMTP_HOST, 465, context=ssl.create_default_context(), timeout=15) as s:
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                s.send_message(msg)
        else:
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                s.send_message(msg)
        return True
    except Exception:
        log.exception("Failed to send email to %s", to)
        return False


def send_verification(user) -> bool:
    link = f"{config.BASE_URL}/verify/{make_token(user.id, user.email)}"
    return send_mail(
        user.email,
        "Verify your email – CheatSheet Hub",
        f"Hi {user.username},\n\nConfirm your email to activate your account (link valid for 2 days):\n{link}\n\n"
        "If you didn't sign up, ignore this email.",
    )
