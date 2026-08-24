"""Authenticated encryption for stored secrets + key rotation."""

from __future__ import annotations

import pytest

from app.platform.secrets import (
    ENC_PREFIX,
    SecretBox,
    SecretDecryptError,
    SecretError,
    SecretKeyMissing,
    generate_key,
    is_encrypted,
    parse_keys,
    secret_box_from_env,
)


def test_round_trip_and_prefix():
    box = SecretBox((generate_key(),))
    ct = box.encrypt("postgresql://user:hunter2@db/app")
    assert ct.startswith(ENC_PREFIX)
    assert "hunter2" not in ct
    assert is_encrypted(ct)
    assert box.decrypt(ct) == "postgresql://user:hunter2@db/app"


def test_ciphertext_is_randomised():
    box = SecretBox((generate_key(),))
    assert box.encrypt("x") != box.encrypt("x")


def test_wrong_key_is_rejected():
    a, b = SecretBox((generate_key(),)), SecretBox((generate_key(),))
    with pytest.raises(SecretDecryptError):
        b.decrypt(a.encrypt("secret"))


def test_tampering_is_detected():
    box = SecretBox((generate_key(),))
    ct = box.encrypt("secret")
    tampered = ct[:-4] + ("AAAA" if not ct.endswith("AAAA") else "BBBB")
    with pytest.raises(SecretDecryptError):
        box.decrypt(tampered)


def test_plaintext_is_not_decryptable():
    box = SecretBox((generate_key(),))
    with pytest.raises(SecretDecryptError):
        box.decrypt("postgresql://plain")


def test_key_rotation_keeps_old_ciphertext_readable():
    old, new = generate_key(), generate_key()
    box_old = SecretBox((old,))
    ct_old = box_old.encrypt("secret")

    box_rotated = SecretBox((new, old))  # new primary, old still accepted
    assert box_rotated.decrypt(ct_old) == "secret"
    assert box_rotated.needs_rotation(ct_old) is True

    ct_new = box_rotated.rotate(ct_old)
    assert box_rotated.needs_rotation(ct_new) is False
    assert SecretBox((new,)).decrypt(ct_new) == "secret"
    with pytest.raises(SecretDecryptError):
        SecretBox((new,)).decrypt(ct_old)


def test_rotate_accepts_plaintext_rows():
    box = SecretBox((generate_key(),))
    assert box.needs_rotation("plaintext-row") is True
    assert box.decrypt(box.rotate("plaintext-row")) == "plaintext-row"


def test_invalid_and_missing_keys():
    with pytest.raises(SecretKeyMissing):
        SecretBox(())
    with pytest.raises(SecretError):
        SecretBox(("not-a-key",))


def test_parse_keys_and_env_factory():
    k1, k2 = generate_key(), generate_key()
    assert parse_keys(f" {k1}, {k2} ,") == (k1, k2)
    assert secret_box_from_env(None) is None
    assert secret_box_from_env("") is None
    box = secret_box_from_env(f"{k1},{k2}")
    assert box is not None and box.keys == (k1, k2)
