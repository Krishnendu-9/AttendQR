"""Temporary passwords that staff hand out after creating or resetting an account."""
import re

from attendqr.security import TEMPORARY_PASSWORD_ALPHABET, generate_temporary_password


def test_temporary_passwords_are_readable_and_random():
    passwords = {generate_temporary_password() for _ in range(50)}
    assert len(passwords) == 50
    for password in passwords:
        assert re.fullmatch(r'[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}', password)
        assert set(password.replace('-', '')) <= set(TEMPORARY_PASSWORD_ALPHABET)
