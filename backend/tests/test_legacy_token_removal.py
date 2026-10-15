"""P1-1: the legacy global Google token path is fully removed from the runtime.

Web re-connect happens per-user via user_integrations; the desktop
token.pickle flow only exists as an isolated one-time migration script
(backend/migrate_legacy_google_token.py).
"""

import pathlib

from tests.conftest import auth
from app.services import gmail_service, google_auth


def _source(mod) -> str:
    return pathlib.Path(mod.__file__).read_text()


def test_no_legacy_global_credential_symbols():
    for name in (
        "load_credentials",
        "save_credentials",
        "reauthorize",
        "_import_legacy_token",
        "probe_credentials",
        "ensure_refreshed",
        "TOKEN_FILE",
    ):
        assert not hasattr(google_auth, name), f"google_auth.{name} still exists"
    assert not hasattr(gmail_service, "reauthorize")


def test_Gmail_module_has_no_pickle_read_path():
    src = _source(gmail_service)
    assert "import pickle" not in src
    assert "pickle.load(" not in src
    assert "token.pickle" not in src


def test_google_auth_module_has_no_pickle_read_path():
    src = _source(google_auth)
    assert "pickle.load(" not in src
    # only comments may mention token.pickle (migration pointer); no code reads it
    code_lines = [ln for ln in src.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert "token.pickle" not in "\n".join(code_lines)


def test_startup_has_no_token_probe_or_import():
    import app.main as main_module

    src = pathlib.Path(main_module.__file__).read_text()
    assert "token.pickle" not in src
    assert "load_credentials" not in src
    assert "_import_legacy_token" not in src


def test_reauth_endpoint_removed(client, owner):
    from app.main import app

    # The literal route must not exist anywhere in the app.
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/emails/re-auth" not in paths

    # POSTing to the dead path is a routing rejection (405), never a working
    # desktop OAuth flow. (GET falls through to the generic /{email_id} route,
    # so we only assert it is not a functional re-auth endpoint.)
    r = client.post("/api/emails/re-auth", headers=auth(owner["token"]))
    assert r.status_code in (404, 405)
    assert client.get("/api/emails/re-auth", headers=auth(owner["token"])).status_code != 200


def test_user_scoped_helpers_remain():
    for name in (
        "credentials_for_user",
        "save_credentials_for_user",
        "ensure_refreshed_for_user",
        "probe_credentials_for_user",
        "integration_row",
    ):
        assert hasattr(google_auth, name), f"user-scoped helper {name} missing"
    assert hasattr(gmail_service, "get_account_email")