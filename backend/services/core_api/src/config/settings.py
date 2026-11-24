"""Runtime configuration, loaded from environment variables or a local .env file.

Values are read from, in increasing priority: ``backend/.env``, ``./.env`` (the
directory you start the app from), and real environment variables.
``load_settings()`` turns missing or invalid values into one readable error that
names the variables but never prints their values.
"""

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SENDER_NAME = "AI CCTV Security"


def env_files() -> tuple[Path, ...]:
    here = Path(__file__).resolve()
    files = [Path(".env")]
    # backend/services/core_api/src/config/settings.py -> backend/.env (absent inside a container image)
    if len(here.parents) > 4:
        files.insert(0, here.parents[4] / ".env")
    return tuple(files)


class ConfigurationError(RuntimeError):
    """Raised at startup when required configuration is missing or invalid."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=env_files(), extra="ignore")

    # Tenant role: organization users, subject to Row-Level Security.
    database_url: str  # postgresql+asyncpg://cctv_app:...@postgres:5432/cctv_security
    # Platform role: Super Admin routes only; no access to tenant tables.
    platform_database_url: str  # postgresql+asyncpg://cctv_platform:...@postgres:5432/cctv_security
    # Owner role: migrations and the Super Admin seed script only.
    migration_database_url: str  # postgresql+psycopg://<owner>:...@postgres:5432/cctv_security

    jwt_secret_key: SecretStr
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)

    # Super Admin login protection: this many failures within the window lock the
    # account for the same window.
    platform_login_max_attempts: int = Field(default=5, ge=1, le=20)
    platform_login_lockout_minutes: int = Field(default=15, ge=1, le=1440)

    # Emergency Super Admin credentials. Optional; set both or neither. Read once at
    # startup, so restart the API after changing them. Never stored in the database,
    # logged, or returned by any endpoint.
    super_admin_recovery_username: str | None = None
    super_admin_recovery_password: SecretStr | None = None

    # "development" allows the local email outbox. Anything else counts as production.
    app_env: Literal["development", "production"] = "production"

    # Email delivery (verification codes):
    #   none        no provider; endpoints that must send email answer 503 EMAIL_DELIVERY_NOT_CONFIGURED
    #   brevo       Brevo HTTP API; needs BREVO_API_KEY and BREVO_SENDER_EMAIL
    #   dev_outbox  write emails to files in DEV_OUTBOX_DIR instead of sending (APP_ENV=development only)
    email_delivery_mode: Literal["none", "brevo", "dev_outbox"] = "none"
    brevo_api_key: SecretStr | None = None
    brevo_sender_email: str | None = None  # must be a sender verified in your Brevo account
    brevo_sender_name: str = DEFAULT_SENDER_NAME
    brevo_api_url: str = "https://api.brevo.com/v3/smtp/email"
    email_send_timeout_seconds: float = Field(default=10.0, ge=1, le=60)
    dev_outbox_dir: Path = Path(".dev_outbox")

    # One-time codes. OTP_SECRET_KEY keys the stored code hashes; if unset, a separate key
    # is derived from JWT_SECRET_KEY. Changing either makes codes already sent stop working.
    otp_secret_key: SecretStr | None = None
    otp_expire_seconds: int = Field(default=300, ge=60, le=1800)
    otp_resend_cooldown_seconds: int = Field(default=60, ge=10, le=600)
    otp_max_attempts: int = Field(default=5, ge=1, le=10)
    otp_hourly_limit: int = Field(default=5, ge=1, le=30)  # codes per email address per hour

    # First Organization Admin registration: how long an unverified registration is kept,
    # and how many registration requests one client IP may make per hour (per API process).
    org_registration_expire_minutes: int = Field(default=60, ge=10, le=1440)
    org_registration_ip_limit_per_hour: int = Field(default=30, ge=1, le=1000)

    # Organization Admin sign-in: wrong passwords within the window lock the account for one
    # window; a sign-in that passed the password check must be finished within
    # ORG_LOGIN_EXPIRE_MINUTES; sign-in requests per client IP per hour (per API process).
    org_login_max_attempts: int = Field(default=5, ge=1, le=20)
    org_login_lockout_minutes: int = Field(default=15, ge=1, le=1440)
    org_login_expire_minutes: int = Field(default=10, ge=2, le=60)
    org_login_ip_limit_per_hour: int = Field(default=60, ge=1, le=10000)

    # Supervisor creation: the Admin's password + code confirmation must be finished within
    # SUPERVISOR_AUTHORIZATION_EXPIRE_MINUTES; after it, the form may be submitted (once) within
    # SUPERVISOR_FORM_EXPIRE_MINUTES; the Supervisor's email code within SUPERVISOR_CREATION_EXPIRE_MINUTES.
    supervisor_authorization_expire_minutes: int = Field(default=10, ge=2, le=60)
    supervisor_form_expire_minutes: int = Field(default=15, ge=1, le=60)
    supervisor_creation_expire_minutes: int = Field(default=30, ge=10, le=1440)
    # A Supervisor's saved profile changes must be confirmed by code within this time.
    supervisor_profile_change_expire_minutes: int = Field(default=15, ge=6, le=60)

    # Password reset (Admins and Supervisors): request lifetime, reset-token lifetime after the
    # code, and reset requests per client IP per hour (per API process).
    password_reset_expire_minutes: int = Field(default=30, ge=10, le=1440)
    password_reset_token_minutes: int = Field(default=10, ge=1, le=60)
    password_reset_ip_limit_per_hour: int = Field(default=30, ge=1, le=10000)

    # Delete Account and ownership transfer: time to enter the emailed code, and how long the
    # recipient has to accept a transfer.
    account_action_verify_minutes: int = Field(default=10, ge=6, le=60)
    ownership_transfer_accept_hours: int = Field(default=72, ge=1, le=336)
    # Campus media files (recordings, clips, evidence, when implemented) live under
    # CCTV_STORAGE_ROOT/<organization>/<campus>/. Deleting a campus erases its folder.
    cctv_storage_root: Path = Path("/mnt/cctv_storage")
    # Platform health (decision D-019): storage use at or above these percentages is a warning / critical.
    storage_warning_percent: int = Field(default=80, ge=1, le=99)
    storage_critical_percent: int = Field(default=90, ge=2, le=100)

    # Alert Management (migration 0015). The AI workers send detections with this shared secret in
    # the X-Detection-Token header; with no token set, the detection intake is switched off (503).
    detection_ingest_token: SecretStr | None = None
    alert_dedup_seconds: int = Field(default=60, ge=0, le=3600)  # repeats merged into an open alert

    # Video ingestion, recordings, retention and the evidence vault (migration 0023, decision D-023).
    # The ingestion service and the retention daemon call /internal/... with X-Service-Token; with no
    # token set those endpoints answer 503. Independent of the detection token.
    internal_service_token: SecretStr | None = None
    recording_retention_default_days: int = Field(default=7, ge=1, le=365)  # campuses without a setting
    live_snapshot_stale_seconds: int = Field(default=15, ge=1, le=600)  # older live frames are not served
    # Automatic escalation of unacknowledged alerts (decision D-024): how often the API checks; 0 = never.
    alert_escalation_poll_seconds: int = Field(default=15, ge=0, le=3600)
    # Person Search (decision D-024): the Re-ID model for photo search, and the result bands when no
    # model manifest gives them. Observations come from the vision workers.
    reid_model_manifest: str | None = None
    reid_likely_threshold: float = Field(default=0.75, gt=0, le=1)
    reid_uncertain_threshold: float = Field(default=0.55, gt=0, le=1)
    person_search_max_results: int = Field(default=50, ge=1, le=500)
    camera_health_check_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    ffmpeg_path: str = "ffmpeg"  # evidence clips and snapshots are cut with ffmpeg
    evidence_clip_pre_seconds: int = Field(default=10, ge=0, le=300)  # from an alert: before the detection
    evidence_clip_post_seconds: int = Field(default=20, ge=1, le=600)  # ... and after its last detection
    evidence_clip_max_seconds: int = Field(default=300, ge=5, le=3600)  # longest clip from playback
    storage_node_name: str = "primary"
    manual_report_photo_max_bytes: int = Field(default=5_000_000, ge=10_000, le=20_000_000)

    # Real-time and in-app communication (decision D-025).
    # The WebSocket at /api/v1/realtime/ws sends signals (IDs only); clients send {"type": "ping"} at least this
    # often, and the server closes a connection that stays silent for twice as long.
    realtime_heartbeat_seconds: int = Field(default=25, ge=5, le=300)
    realtime_auth_timeout_seconds: int = Field(default=10, ge=2, le=60)  # first message must authenticate
    # In-app voice calls about an alert or a case:
    #   none     calls are refused with 503 VOICE_CALL_NOT_CONFIGURED (the default)
    #   livekit  LiveKit Cloud (free tier) or a self-hosted LiveKit server; needs LIVEKIT_URL (wss://...),
    #            LIVEKIT_API_KEY and LIVEKIT_API_SECRET. The API only signs short-lived room tokens.
    voice_call_provider: Literal["none", "livekit"] = "none"
    livekit_url: str | None = None
    livekit_api_key: SecretStr | None = None
    livekit_api_secret: SecretStr | None = None
    livekit_token_ttl_seconds: int = Field(default=900, ge=60, le=86_400)
    livekit_request_timeout_seconds: float = Field(default=5.0, gt=0, le=30)  # closing a room (best effort)
    # Push delivery to locked phones:
    #   none  nothing is pushed; device tokens are still stored (the default)
    #   fcm   Firebase Cloud Messaging HTTP v1 (Android); needs FCM_PROJECT_ID and FCM_SERVICE_ACCOUNT_FILE (the
    #         service-account JSON key; keep it outside the repository)
    push_delivery_mode: Literal["none", "fcm"] = "none"
    fcm_project_id: str | None = None
    fcm_service_account_file: str | None = None
    push_poll_seconds: int = Field(default=5, ge=1, le=300)
    push_max_age_seconds: int = Field(default=600, ge=10, le=86_400)  # older notifications are never pushed
    push_request_timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    # Read-only AI Investigation & Security Analytics Chatbot (decision D-027; /api/v1/assistant/*).
    #   deterministic      rule-based planner + grounded answer writer; offline, no keys, no downloads (default)
    #   ollama             a local Ollama server with Qwen2.5-7B-Instruct (qwen2.5:7b) or the fine-tuned adapter
    #   openai_compatible  any /v1/chat/completions endpoint (vLLM, SGLang, or external with AI_LLM_ALLOW_EXTERNAL)
    # Every number comes from SQL under the caller's Row-Level Security; a model only plans and rephrases, and only
    # redacted summaries are sent to it (never video, frames, passwords, codes, tokens, emails or phone numbers).
    ai_llm_provider: Literal["deterministic", "ollama", "openai_compatible"] = "deterministic"
    ai_llm_base_url: str | None = None  # default for ollama: http://127.0.0.1:11434
    ai_llm_model: str = Field(default="qwen2.5:7b", min_length=1, max_length=120)
    ai_llm_api_key: SecretStr | None = None
    ai_llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    ai_llm_allow_external: bool = False  # a non-local AI_LLM_BASE_URL is refused unless this is true
    ai_chat_retention_days: int = Field(default=30, ge=1, le=3650)  # inactive chat sessions are purged after this

    # Personnel import (decision D-016). There is no row limit: files are read as a stream. These
    # protect the server only, and an upload over a limit is refused as a whole, with a clear error:
    #   PERSONNEL_IMPORT_MAX_UPLOAD_BYTES        size of the uploaded .xlsx / .csv file
    #   PERSONNEL_IMPORT_MAX_UNCOMPRESSED_BYTES  total unpacked size of an .xlsx (an .xlsx is a zip)
    #   PERSONNEL_IMPORT_FILE_RETENTION_HOURS    how long uploaded and annotated files are kept;
    #                                            unconfirmed imports then expire (history stays)
    #   PERSONNEL_IMPORT_CHUNK_ROWS              rows per database round trip (not a limit)
    personnel_import_max_upload_bytes: int = Field(default=100_000_000, ge=10_000, le=2_000_000_000)
    personnel_import_max_uncompressed_bytes: int = Field(default=1_000_000_000, ge=100_000, le=20_000_000_000)
    personnel_import_file_retention_hours: int = Field(default=72, ge=1, le=24 * 90)
    personnel_import_chunk_rows: int = Field(default=1000, ge=50, le=20_000)
    personnel_photo_max_bytes: int = Field(default=5_000_000, ge=10_000, le=20_000_000)

    @field_validator(
        "super_admin_recovery_username",
        "super_admin_recovery_password",
        "brevo_api_key",
        "brevo_sender_email",
        "otp_secret_key",
        "livekit_url",
        "livekit_api_key",
        "livekit_api_secret",
        "fcm_project_id",
        "fcm_service_account_file",
        "ai_llm_base_url",
        "ai_llm_api_key",
        mode="before",
    )
    @classmethod
    def _empty_means_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("brevo_sender_name", mode="before")
    @classmethod
    def _empty_means_default_name(cls, value: object) -> object:
        return DEFAULT_SENDER_NAME if value in ("", None) else value

    @model_validator(mode="after")
    def _email_delivery_is_complete(self) -> "Settings":
        if self.email_delivery_mode == "brevo":
            missing = [
                name
                for name, value in (
                    ("BREVO_API_KEY", self.brevo_api_key),
                    ("BREVO_SENDER_EMAIL", self.brevo_sender_email),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"EMAIL_DELIVERY_MODE=brevo needs {' and '.join(missing)}")
            if not self.brevo_api_url.startswith("https://"):
                raise ValueError("BREVO_API_URL must start with https://")
        if self.email_delivery_mode == "dev_outbox" and self.app_env != "development":
            raise ValueError("EMAIL_DELIVERY_MODE=dev_outbox is allowed only when APP_ENV=development")
        if self.otp_secret_key is not None and len(self.otp_secret_key.get_secret_value()) < 32:
            raise ValueError("OTP_SECRET_KEY must be at least 32 characters long")
        if self.org_registration_expire_minutes * 60 <= self.otp_expire_seconds:
            raise ValueError("ORG_REGISTRATION_EXPIRE_MINUTES must be longer than OTP_EXPIRE_SECONDS")
        if self.org_login_expire_minutes * 60 <= self.otp_expire_seconds:
            raise ValueError("ORG_LOGIN_EXPIRE_MINUTES must be longer than OTP_EXPIRE_SECONDS")
        if self.account_action_verify_minutes * 60 <= self.otp_expire_seconds:
            raise ValueError("ACCOUNT_ACTION_VERIFY_MINUTES must be longer than OTP_EXPIRE_SECONDS")
        if self.supervisor_authorization_expire_minutes * 60 <= self.otp_expire_seconds:
            raise ValueError("SUPERVISOR_AUTHORIZATION_EXPIRE_MINUTES must be longer than OTP_EXPIRE_SECONDS")
        return self

    @model_validator(mode="after")
    def _communication_is_complete(self) -> "Settings":
        if self.voice_call_provider == "livekit":
            missing = [
                name
                for name, value in (
                    ("LIVEKIT_URL", self.livekit_url),
                    ("LIVEKIT_API_KEY", self.livekit_api_key),
                    ("LIVEKIT_API_SECRET", self.livekit_api_secret),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"VOICE_CALL_PROVIDER=livekit needs {' and '.join(missing)}")
            assert self.livekit_url is not None
            if not self.livekit_url.startswith(("wss://", "ws://")):
                raise ValueError("LIVEKIT_URL must start with wss:// (or ws:// for a local test server)")
        if self.push_delivery_mode == "fcm":
            missing = [
                name
                for name, value in (
                    ("FCM_PROJECT_ID", self.fcm_project_id),
                    ("FCM_SERVICE_ACCOUNT_FILE", self.fcm_service_account_file),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"PUSH_DELIVERY_MODE=fcm needs {' and '.join(missing)}")
        return self

    @model_validator(mode="after")
    def _recovery_is_complete(self) -> "Settings":
        username, password = self.super_admin_recovery_username, self.super_admin_recovery_password
        if (username is None) != (password is None):
            raise ValueError("set both SUPER_ADMIN_RECOVERY_USERNAME and SUPER_ADMIN_RECOVERY_PASSWORD, or neither")
        if username is not None and not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username):
            raise ValueError("SUPER_ADMIN_RECOVERY_USERNAME must be 3-64 letters, digits, '.', '_' or '-'")
        if password is not None and len(password.get_secret_value()) < 8:
            raise ValueError("SUPER_ADMIN_RECOVERY_PASSWORD must be at least 8 characters long")
        return self

    @field_validator("jwt_secret_key")
    @classmethod
    def _secret_is_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("must be at least 32 characters long")
        return value


def describe_validation_error(exc: ValidationError) -> list[str]:
    """One line per problem, naming the environment variable but never its value."""
    problems = []
    for error in exc.errors():
        name = str(error["loc"][0]).upper() if error["loc"] else "SETTINGS"
        if error["type"] == "missing":
            problems.append(f"{name} is not set")
        else:
            problems.append(f"{name}: {error['msg']}")
    return problems


def load_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]  # values come from the environment
    except ValidationError as exc:
        lines = "\n  - ".join(describe_validation_error(exc))
        raise ConfigurationError(
            "Invalid configuration for core_api:\n  - "
            f"{lines}\n"
            "Copy backend/.env.example to backend/.env and fill in the missing values."
        ) from None
