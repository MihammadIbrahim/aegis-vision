from src.domain.value_objects.recovery_credentials import RecoveryCredentials


def test_matches_needs_both_values_and_username_ignores_case() -> None:
    creds = RecoveryCredentials(username="admin", password="admin@123")
    assert creds.matches("ADMIN", "admin@123")
    assert not creds.matches("admin", "Admin@123")  # passwords are case-sensitive
    assert not creds.matches("other", "admin@123")
    assert creds.username_matches("Admin") and not creds.username_matches("root")


def test_password_never_appears_in_repr_or_str() -> None:
    creds = RecoveryCredentials(username="admin", password="super-secret-value")
    assert "super-secret-value" not in repr(creds) and "super-secret-value" not in str(creds)
