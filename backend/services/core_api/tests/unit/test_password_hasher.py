from src.infrastructure.security.password import Argon2PasswordHasher


def test_hash_is_argon2id_and_never_the_password() -> None:
    hasher = Argon2PasswordHasher()
    hashed = hasher.hash("a long example password")
    assert hashed.startswith("$argon2id$")
    assert "a long example password" not in hashed
    assert hasher.hash("a long example password") != hashed  # random salt


def test_verify_accepts_only_the_right_password() -> None:
    hasher = Argon2PasswordHasher()
    hashed = hasher.hash("right password 123")
    assert hasher.verify(hashed, "right password 123")
    assert not hasher.verify(hashed, "wrong password 123")


def test_verify_returns_false_for_malformed_hash() -> None:
    assert not Argon2PasswordHasher().verify("not-a-hash", "anything")
