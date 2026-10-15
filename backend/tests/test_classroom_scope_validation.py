"""Classroom scope validation matches the API operations the app performs.

``classroom_service.list_assignments`` and ``list_submissions`` call
``courses.courseWork.list``, which is authorized ONLY by a coursework scope
(``classroom.coursework.me.readonly``). ``classroom.student-submissions.me.
readonly`` cannot substitute for it — the API returns HTTP 403.

Existing tokens may carry the courses + submissions scopes but NOT the
coursework scope because they were authorized before assignment support was
enabled. In that case:

* ``_get_user_service`` / ``list_courses`` still work (courses scope present).
* ``list_assignments`` / ``list_submissions`` raise ``ClassroomScopeError``
  (a graceful, per-feature unavailability) instead of a misleading 403 that
  would mark the whole integration dead.
"""

import asyncio
import types

import pytest

from app.services import classroom_service
from app.services.classroom_service import (
    CLASSROOM_COURSES_SCOPE,
    CLASSROOM_COURSEWORK_SCOPE,
    ClassroomAuthError,
    ClassroomScopeError,
)
from app.services.google_auth import has_scope

SUBMISSIONS_SCOPE = (
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly"
)


def _install_creds(monkeypatch, scopes):
    creds = types.SimpleNamespace(scopes=list(scopes))
    monkeypatch.setattr(
        classroom_service, "credentials_for_user", lambda *a, **k: creds
    )

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(classroom_service, "ensure_refreshed_for_user", _noop)
    sentinel = object()
    monkeypatch.setattr(classroom_service, "build_service", lambda *a, **k: sentinel)
    return sentinel


def test_submissions_scope_does_not_satisfy_coursework():
    creds = types.SimpleNamespace(scopes=[SUBMISSIONS_SCOPE])
    assert has_scope(creds, SUBMISSIONS_SCOPE)
    assert not has_scope(creds, CLASSROOM_COURSEWORK_SCOPE)


def test_get_user_service_needs_only_courses_scope(db, owner, monkeypatch):
    """A token with courses + submissions (no coursework) can still build a
    service and list courses."""
    sentinel = _install_creds(monkeypatch, [CLASSROOM_COURSES_SCOPE, SUBMISSIONS_SCOPE])
    got = asyncio.run(classroom_service._get_user_service(db, user_id=owner["id"]))
    assert got is sentinel


def test_get_user_service_rejects_missing_courses_scope(db, owner, monkeypatch):
    _install_creds(monkeypatch, [SUBMISSIONS_SCOPE])
    with pytest.raises(ClassroomAuthError):
        asyncio.run(classroom_service._get_user_service(db, user_id=owner["id"]))


def test_list_assignments_requires_coursework_scope(db, owner, monkeypatch):
    _install_creds(monkeypatch, [CLASSROOM_COURSES_SCOPE, SUBMISSIONS_SCOPE])
    with pytest.raises(ClassroomScopeError):
        asyncio.run(classroom_service.list_assignments(db=db, user_id=owner["id"]))


def test_list_submissions_requires_coursework_scope(db, owner, monkeypatch):
    _install_creds(monkeypatch, [CLASSROOM_COURSES_SCOPE, SUBMISSIONS_SCOPE])
    with pytest.raises(ClassroomScopeError):
        asyncio.run(classroom_service.list_submissions(db=db, user_id=owner["id"]))


def test_list_assignments_pass_with_coursework_scope(db, owner, monkeypatch):
    """With the coursework scope present the scope check passes and the (mocked)
    course list yields no assignments."""
    _install_creds(monkeypatch, [CLASSROOM_COURSES_SCOPE, CLASSROOM_COURSEWORK_SCOPE])

    async def _fake_courses(*a, **k):
        return []

    monkeypatch.setattr(classroom_service, "list_courses", _fake_courses)
    result = asyncio.run(classroom_service.list_assignments(db=db, user_id=owner["id"]))
    assert result == []
