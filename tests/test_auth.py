import json
import os
import stat
import time

import pytest

from simpleaprs import auth as authmod
from simpleaprs.auth import Auth


@pytest.fixture
def auth(tmp_path):
    return Auth(tmp_path / "auth.json")


def test_first_visit_creates_the_only_account(auth):
    assert not auth.configured
    assert auth.create("alice", "correct horse") == ""
    assert auth.configured and auth.username == "alice" and auth.api_token
    assert "already" in auth.create("mallory", "another password")  # nobody can take over afterwards
    assert auth.username == "alice"


def test_rejects_weak_or_odd_credentials(auth):
    assert auth.create("alice", "short")
    assert auth.create("", "long enough password")
    assert auth.create("x" * 33, "long enough password")
    assert not auth.configured


def test_password_is_hashed_and_file_is_private(auth, tmp_path):
    auth.create("alice", "correct horse")
    raw = (tmp_path / "auth.json").read_text()
    assert "correct horse" not in raw
    assert stat.S_IMODE(os.stat(tmp_path / "auth.json").st_mode) == 0o600


def test_login_check(auth):
    auth.create("alice", "correct horse")
    assert auth.check("alice", "correct horse", "1.2.3.4") == (True, 0)
    assert auth.check(" alice ", "correct horse", "1.2.3.4")[0]
    assert auth.check("alice", "wrong", "1.2.3.4") == (False, 0)
    assert auth.check("someone", "correct horse", "1.2.3.4") == (False, 0)


def test_login_before_setup_never_succeeds(auth):
    assert auth.check("", "", "1.2.3.4") == (False, 0)
    assert auth.check("alice", "anything", "1.2.3.4") == (False, 0)


def test_lockout_after_repeated_failures(auth):
    auth.create("alice", "correct horse")
    for _ in range(authmod.MAX_FAILS):
        assert auth.check("alice", "nope", "9.9.9.9")[0] is False
    ok, wait = auth.check("alice", "correct horse", "9.9.9.9")  # even the right password is refused while locked out
    assert not ok and wait > 0
    assert auth.check("alice", "correct horse", "5.5.5.5")[0]  # someone else is not affected


def test_session_lifetimes(auth, monkeypatch):
    auth.create("alice", "correct horse")
    plain, plain_age = auth.start_session(remember=False)
    kept, kept_age = auth.start_session(remember=True)
    assert plain_age is None and kept_age == authmod.REMEMBER_S  # browser-session cookie vs. 30 days
    assert auth.valid(plain) and auth.valid(kept) and not auth.valid("") and not auth.valid("made up")
    now = time.time()
    monkeypatch.setattr(authmod.time, "time", lambda: now + authmod.SESSION_S + 60)
    assert not auth.valid(plain) and auth.valid(kept)
    monkeypatch.setattr(authmod.time, "time", lambda: now + authmod.REMEMBER_S + 60)
    assert not auth.valid(kept)


def test_remembered_session_is_renewed_when_used(auth, monkeypatch):
    auth.create("alice", "correct horse")
    kept, _ = auth.start_session(True)
    now = time.time()
    monkeypatch.setattr(authmod.time, "time", lambda: now + 20 * 86400)
    assert auth.valid(kept)  # used on day 20: now good for 30 days from then
    monkeypatch.setattr(authmod.time, "time", lambda: now + 45 * 86400)
    assert auth.valid(kept)


def test_sessions_survive_a_restart_and_only_hashes_are_stored(auth, tmp_path):
    auth.create("alice", "correct horse")
    token, _ = auth.start_session(True)
    again = Auth(tmp_path / "auth.json")
    assert again.valid(token) and again.check("alice", "correct horse", "x")[0]
    assert token not in (tmp_path / "auth.json").read_text()


def test_logout_ends_one_session(auth):
    auth.create("alice", "correct horse")
    a, _ = auth.start_session(False)
    b, _ = auth.start_session(True)
    auth.end(a)
    assert not auth.valid(a) and auth.valid(b)
    auth.end_all()
    assert not auth.valid(b)


def test_change_password_keeps_only_the_callers_session(auth):
    auth.create("alice", "correct horse")
    mine, _ = auth.start_session(False)
    other, _ = auth.start_session(True)
    assert "wrong" in auth.change_password("not it", "brand new password", keep=mine)
    assert auth.change_password("correct horse", "short", keep=mine)
    assert auth.change_password("correct horse", "brand new password", keep=mine) == ""
    assert auth.valid(mine) and not auth.valid(other)
    assert auth.check("alice", "brand new password", "x")[0] and not auth.check("alice", "correct horse", "y")[0]


def test_access_token_can_be_replaced(auth):
    auth.create("alice", "correct horse")
    old = auth.api_token
    assert auth.token_ok(old) and not auth.token_ok("") and not auth.token_ok("nope")
    new = auth.new_api_token()
    assert new != old and auth.token_ok(new) and not auth.token_ok(old)


def test_reset_forgets_everything(auth, tmp_path):
    auth.create("alice", "correct horse")
    token, _ = auth.start_session(True)
    auth.reset()
    assert not auth.configured and not auth.valid(token) and not (tmp_path / "auth.json").exists()
    assert auth.create("new", "another password") == ""


def test_corrupt_file_means_not_set_up(tmp_path):
    (tmp_path / "auth.json").write_text("{not json")
    assert not Auth(tmp_path / "auth.json").configured
