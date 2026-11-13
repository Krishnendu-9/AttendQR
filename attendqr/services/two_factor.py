"""Two-step sign-in for staff: a 6-digit code from an authenticator app (TOTP, RFC 6238).

Works with any authenticator: Google, Microsoft, Zoho, 2FAS, Aegis, password managers.

- Setup: a new secret waits in totp_pending_secret, shown as a QR code, until the user
  types a code from their app. Only then does it become totp_secret, so a half-finished
  setup never locks anyone out.
- A code is accepted for its 30-second step and one step either side (clock drift),
  and each step only once (totp_last_step), so a code seen over a shoulder cannot be reused.
- Ten single-use recovery codes, stored as SHA-256 hashes (they are random, so a fast
  hash is enough), for a lost phone.
- TOTP_REQUIRED_ROLES (default: Principal and HOD) must set it up before using the app;
  other staff may turn it on. Students never use it.
"""
import hashlib
import hmac
import json
import re
import secrets
import time

import pyotp
from flask import current_app

from ..security import TEMPORARY_PASSWORD_ALPHABET
from ..utils import utc_now

STEP_SECONDS = 30
RECOVERY_CODE_COUNT = 10
STAFF_ROLES = ('principal', 'hod', 'teacher')


def is_required(user):
    return user.role in current_app.config['TOTP_REQUIRED_ROLES']


def can_use(user):
    return user.role in STAFF_ROLES


def is_enabled(user):
    return bool(user.totp_secret)


# ── Setup ────────────────────────────────────────────────────

def pending_secret(user):
    """The secret being set up, created on first use."""
    if not user.totp_pending_secret:
        user.totp_pending_secret = pyotp.random_base32()
    return user.totp_pending_secret


def provisioning_uri(user, secret):
    """otpauth:// link encoded in the setup QR code."""
    return pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name=current_app.config['TOTP_ISSUER'])


def grouped(secret):
    """The key as the user types it by hand: groups of four."""
    return ' '.join(secret[i:i + 4] for i in range(0, len(secret), 4))


def enable(user, code):
    """Turn on two-step sign-in if `code` matches the pending secret. Returns the recovery codes, or None."""
    secret = user.totp_pending_secret
    if not secret or not _accept(user, secret, code):
        return None
    user.totp_secret, user.totp_pending_secret = secret, None
    user.totp_enabled_at = utc_now()
    return new_recovery_codes(user)


def disable(user):
    user.totp_secret = user.totp_pending_secret = user.recovery_codes = None
    user.totp_enabled_at = user.totp_last_step = None


# ── Codes ────────────────────────────────────────────────────

def _digits(code):
    return re.sub(r'\s', '', str(code or ''))


def _accept(user, secret, code):
    code = _digits(code)
    if not re.fullmatch(r'\d{6}', code):
        return False
    totp = pyotp.TOTP(secret)
    now_step = int(time.time()) // STEP_SECONDS
    for step in (now_step - 1, now_step, now_step + 1):
        if user.totp_last_step is not None and step <= user.totp_last_step:
            continue  # already used, or older than a code that was
        if hmac.compare_digest(totp.at(step * STEP_SECONDS), code):
            user.totp_last_step = step
            return True
    return False


def verify_code(user, code):
    """A current authenticator code for an account with two-step sign-in on. Marks it used."""
    return is_enabled(user) and _accept(user, user.totp_secret, code)


# ── Recovery codes ───────────────────────────────────────────

def _normalise_recovery(code):
    return re.sub(r'[^A-Z0-9]', '', str(code or '').upper())


def _hash(code):
    return hashlib.sha256(_normalise_recovery(code).encode()).hexdigest()


def new_recovery_codes(user):
    """Replace the recovery codes; returns the new ones (shown once, never stored in clear)."""
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        chars = ''.join(secrets.choice(TEMPORARY_PASSWORD_ALPHABET) for _ in range(10))
        codes.append(f'{chars[:5]}-{chars[5:]}')
    user.recovery_codes = json.dumps([_hash(code) for code in codes])
    return codes


def recovery_codes_left(user):
    return len(json.loads(user.recovery_codes or '[]'))


def use_recovery_code(user, code):
    """Sign in with a recovery code instead of an authenticator code; each works once."""
    if not is_enabled(user) or len(_normalise_recovery(code)) != 10:
        return False
    hashes = json.loads(user.recovery_codes or '[]')
    wanted = _hash(code)
    for stored in hashes:
        if hmac.compare_digest(stored, wanted):
            hashes.remove(stored)
            user.recovery_codes = json.dumps(hashes)
            return True
    return False
