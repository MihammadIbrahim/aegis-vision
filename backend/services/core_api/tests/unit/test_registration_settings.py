"""Settings for email delivery, one-time codes and registration."""

import pytest

from src.config.settings import ConfigurationError, Settings, load_settings

BASE = {
    "DATABASE_URL": "postgresql+asyncpg://a@h/d",
    "PLATFORM_DATABASE_URL": "postgresql+asyncpg://p@h/d",
    "MIGRATION_DATABASE_URL": "postgresql+psycopg://o@h/d",
    "JWT_SECRET_KEY": "x" * 32,
}
NEW = [
    "APP_ENV",
    "EMAIL_DELIVERY_MODE",
    "BREVO_API_KEY",
    "BREVO_SENDER_EMAIL",
    "BREVO_SENDER_NAME",
    "BREVO_API_URL",
    "OTP_SECRET_KEY",
    "OTP_EXPIRE_SECONDS",
    "ORG_REGISTRATION_EXPIRE_MINUTES",
]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> pytest.MonkeyPatch:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Settings, "model_config", {**Settings.model_config, "env_file": None})
    for name in NEW:
        monkeypatch.delenv(name, raising=False)
    for name, value in BASE.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_defaults_are_safe(env: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert settings.app_env == "production"
    assert settings.email_delivery_mode == "none"
    assert (settings.otp_expire_seconds, settings.otp_resend_cooldown_seconds) == (300, 60)
    assert (settings.otp_max_attempts, settings.otp_hourly_limit) == (5, 5)
    assert settings.org_registration_expire_minutes == 60


def test_blank_values_from_env_example_mean_unset(env: pytest.MonkeyPatch) -> None:
    for name in ("BREVO_API_KEY", "BREVO_SENDER_EMAIL", "BREVO_SENDER_NAME", "OTP_SECRET_KEY"):
        env.setenv(name, "")
    settings = load_settings()
    assert settings.brevo_api_key is None and settings.otp_secret_key is None
    assert settings.brevo_sender_name == "AI CCTV Security"


def test_brevo_mode_names_what_is_missing_without_printing_values(env: pytest.MonkeyPatch) -> None:
    env.setenv("EMAIL_DELIVERY_MODE", "brevo")
    env.setenv("BREVO_SENDER_EMAIL", "no-reply@example.com")
    with pytest.raises(ConfigurationError) as error:
        load_settings()
    assert "EMAIL_DELIVERY_MODE=brevo needs BREVO_API_KEY" in str(error.value)

    env.setenv("BREVO_API_KEY", "xkeysib-secret-value")
    env.setenv("BREVO_API_URL", "http://api.brevo.com/v3/smtp/email")
    with pytest.raises(ConfigurationError) as error:
        load_settings()
    assert "https://" in str(error.value) and "xkeysib-secret-value" not in str(error.value)


def test_complete_brevo_configuration_loads(env: pytest.MonkeyPatch) -> None:
    env.setenv("EMAIL_DELIVERY_MODE", "brevo")
    env.setenv("BREVO_API_KEY", "xkeysib-secret-value")
    env.setenv("BREVO_SENDER_EMAIL", "no-reply@example.com")
    settings = load_settings()
    assert settings.brevo_api_key is not None and "xkeysib" not in repr(settings)


def test_dev_outbox_is_refused_outside_development(env: pytest.MonkeyPatch) -> None:
    env.setenv("EMAIL_DELIVERY_MODE", "dev_outbox")
    with pytest.raises(ConfigurationError) as error:
        load_settings()
    assert "only when APP_ENV=development" in str(error.value)
    env.setenv("APP_ENV", "development")
    assert load_settings().email_delivery_mode == "dev_outbox"


def test_short_otp_secret_is_refused_without_printing_it(env: pytest.MonkeyPatch) -> None:
    env.setenv("OTP_SECRET_KEY", "too-short-otp-secret")
    with pytest.raises(ConfigurationError) as error:
        load_settings()
    assert "OTP_SECRET_KEY must be at least 32 characters" in str(error.value)
    assert "too-short-otp-secret" not in str(error.value)


def test_registration_must_outlive_its_code(env: pytest.MonkeyPatch) -> None:
    env.setenv("ORG_REGISTRATION_EXPIRE_MINUTES", "10")
    env.setenv("OTP_EXPIRE_SECONDS", "600")
    with pytest.raises(ConfigurationError, match="ORG_REGISTRATION_EXPIRE_MINUTES must be longer"):
        load_settings()
