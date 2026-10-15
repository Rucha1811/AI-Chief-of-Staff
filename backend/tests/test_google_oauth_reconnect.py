"""Regression: reconnecting Google must work after a previous disconnect.

``disconnect_user_google`` marks the user's google integration ``revoked``
(tokens kept, inactive). The web-OAuth callback resolves the owning user by
scanning rows whose status is ``active``/``pending`` (see
``app.api.google_oauth._resolve_user_by_state``). ``_store_pending_state`` used
to leave an existing row's status untouched, so a revoked row stayed revoked
and the callback returned ``reason=unknown_state`` — the account could never be
reconnected.

Re-authorizing now moves a non-active row back to ``pending`` for the duration
of the flow, while leaving an already-active connection untouched.
"""

from app.api.google_oauth import _resolve_user_by_state
from app.models.user_integration import UserIntegration
from app.services import google_auth


def _google_row(db, user_id):
    return (
        db.query(UserIntegration)
        .filter(
            UserIntegration.user_id == user_id,
            UserIntegration.provider == "google",
        )
        .one()
    )


def test_reauth_from_revoked_row_is_resolvable(db, owner):
    db.add(
        UserIntegration(user_id=owner["id"], provider="google", status="revoked")
    )
    db.commit()

    state = "reconnect-nonce-revoked"
    google_auth._store_pending_state(db, owner["id"], state)

    # The row is discoverable again by the callback's active/pending scan.
    assert _google_row(db, owner["id"]).status == "pending"
    assert _resolve_user_by_state(db, state) == owner["id"]
    # A wrong/unknown nonce must never resolve to a user.
    assert _resolve_user_by_state(db, "some-other-nonce") is None


def test_reauth_keeps_active_row_active(db, owner):
    db.add(
        UserIntegration(user_id=owner["id"], provider="google", status="active")
    )
    db.commit()

    state = "reconnect-nonce-active"
    google_auth._store_pending_state(db, owner["id"], state)

    # Re-auth must not disable an already-working connection.
    assert _google_row(db, owner["id"]).status == "active"
    assert _resolve_user_by_state(db, state) == owner["id"]
