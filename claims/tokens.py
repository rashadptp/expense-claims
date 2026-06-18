"""
Signed, expiring tokens for one-click email approvals.

A token embeds the claim id, the approver's user id, and the claim's status at
the moment the email was sent. The action view re-checks all three, so a link:
  * can't be tampered with (HMAC-signed by SECRET_KEY),
  * expires after MAX_AGE,
  * only works while the claim is still at the stage the approver was emailed
    about (the embedded status must match), and
  * only lets the intended approver act (embedded user id + can_act_on check).
"""
from django.core import signing

SALT = "claim-email-action"
MAX_AGE = 7 * 24 * 3600  # 7 days


def make_action_token(claim, user) -> str:
    return signing.dumps(
        {"c": claim.pk, "u": user.pk, "s": str(claim.status)}, salt=SALT
    )


def load_action_token(token: str) -> dict:
    """Raises signing.BadSignature / SignatureExpired on bad or old tokens."""
    return signing.loads(token, salt=SALT, max_age=MAX_AGE)
