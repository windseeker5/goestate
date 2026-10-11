"""Web Push notifications for the installed PWA.

One row in push_subscriptions per subscribed device (see app/commands.py).
Sending is best-effort: a push failure must never break the request that
triggered it (creating an asset, etc.), so notify_all() swallows per-device
errors and just prunes subscriptions the push service reports as gone.
"""

import json

from flask import current_app


def notify_all(db, title, body, url=None):
    """Push a notification to every subscribed device. Safe to call unconditionally."""
    public_key = current_app.config.get("VAPID_PUBLIC_KEY")
    private_key = current_app.config.get("VAPID_PRIVATE_KEY")
    claims_email = current_app.config.get("VAPID_CLAIMS_EMAIL")
    if not (public_key and private_key and claims_email):
        return

    from pywebpush import WebPushException, webpush

    payload = json.dumps({"title": title, "body": body, "url": url or "/app"})

    for sub in db.execute("SELECT * FROM push_subscriptions").fetchall():
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": claims_email},
            )
        except WebPushException as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (404, 410):
                # The push service says this subscription is gone (browser
                # data cleared, app uninstalled, etc.) — stop trying it.
                db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (sub["endpoint"],))
                db.commit()
            else:
                current_app.logger.warning("Push send failed for %s: %s", sub["endpoint"], exc)
