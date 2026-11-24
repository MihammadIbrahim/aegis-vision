import pytest

from src.config.settings import ConfigurationError, Settings, load_settings

REQUIRED = ["DATABASE_URL", "PLATFORM_DATABASE_URL", "MIGRATION_DATABASE_URL", "JWT_SECRET_KEY"]


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> pytest.MonkeyPatch:
    """No real .env and none of the variables set."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Settings, "model_config", {**Settings.model_config, "env_file": None})
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_missing_variables_are_all_named(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigurationError) as error:
        load_settings()
    for name in REQUIRED:
        assert f"{name} is not set" in str(error.value)
    assert ".env.example" in str(error.value)


def test_short_jwt_secret_is_rejected_without_printing_it(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://a@h/d")
    clean_env.setenv("PLATFORM_DATABASE_URL", "postgresql+asyncpg://p@h/d")
    clean_env.setenv("MIGRATION_DATABASE_URL", "postgresql+psycopg://o@h/d")
    clean_env.setenv("JWT_SECRET_KEY", "too-short-secret")
    with pytest.raises(ConfigurationError) as error:
        load_settings()
    assert "JWT_SECRET_KEY: Value error, must be at least 32 characters long" in str(error.value)
    assert "too-short-secret" not in str(error.value)


def test_complete_configuration_loads(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://a@h/d")
    clean_env.setenv("PLATFORM_DATABASE_URL", "postgresql+asyncpg://p@h/d")
    clean_env.setenv("MIGRATION_DATABASE_URL", "postgresql+psycopg://o@h/d")
    clean_env.setenv("JWT_SECRET_KEY", "x" * 32)
    settings = load_settings()
    assert settings.platform_login_max_attempts == 5


def _base_env(env: pytest.MonkeyPatch) -> None:
    env.setenv("DATABASE_URL", "postgresql+asyncpg://a@h/d")
    env.setenv("PLATFORM_DATABASE_URL", "postgresql+asyncpg://p@h/d")
    env.setenv("MIGRATION_DATABASE_URL", "postgresql+psycopg://o@h/d")
    env.setenv("JWT_SECRET_KEY", "x" * 32)


def test_recovery_credentials_are_optional_and_blank_means_unset(clean_env: pytest.MonkeyPatch) -> None:
    _base_env(clean_env)
    clean_env.setenv("SUPER_ADMIN_RECOVERY_USERNAME", "")
    clean_env.setenv("SUPER_ADMIN_RECOVERY_PASSWORD", "")
    settings = load_settings()
    assert settings.super_admin_recovery_username is None and settings.super_admin_recovery_password is None


def test_recovery_needs_both_values_and_never_prints_the_password(clean_env: pytest.MonkeyPatch) -> None:
    _base_env(clean_env)
    clean_env.delenv("SUPER_ADMIN_RECOVERY_USERNAME", raising=False)
    clean_env.setenv("SUPER_ADMIN_RECOVERY_PASSWORD", "only-the-password-set")
    with pytest.raises(ConfigurationError, match="set both SUPER_ADMIN_RECOVERY_USERNAME") as error:
        load_settings()
    assert "only-the-password-set" not in str(error.value)


def test_recovery_values_load_when_both_are_set(clean_env: pytest.MonkeyPatch) -> None:
    _base_env(clean_env)
    clean_env.setenv("SUPER_ADMIN_RECOVERY_USERNAME", "admin")
    clean_env.setenv("SUPER_ADMIN_RECOVERY_PASSWORD", "admin@123")
    settings = load_settings()
    assert settings.super_admin_recovery_username == "admin"
    assert "admin@123" not in repr(settings)
