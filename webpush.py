"""Web push (plan A only). Plan B on GitHub Pages has no web push: Telegram carries all pushes.
VAPID private key stays in /home/box/.desk_secrets and is never published."""
from config import secret

def enabled(hosting: str) -> bool:
    return hosting == "planA" and bool(secret("VAPID_PRIVATE_KEY_FILE"))

def send(subscription: dict, payload: dict) -> bool:
    from pywebpush import webpush
    webpush(subscription_info=subscription, data=__import__("json").dumps(payload),
            vapid_private_key=secret("VAPID_PRIVATE_KEY_FILE"), vapid_claims={"sub": "mailto:desk@localhost"})
    return True
