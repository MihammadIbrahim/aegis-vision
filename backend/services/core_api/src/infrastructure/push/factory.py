from src.application.interfaces.i_push_sender import IPushSender
from src.config.settings import Settings
from src.infrastructure.push.fcm_sender import FcmPushSender, NoPushSender


def build_push_sender(settings: Settings) -> IPushSender:
    """Chosen by PUSH_DELIVERY_MODE (settings validation guarantees the FCM values when selected)."""
    if settings.push_delivery_mode == "fcm":
        assert settings.fcm_project_id and settings.fcm_service_account_file
        return FcmPushSender.from_file(
            settings.fcm_project_id, settings.fcm_service_account_file, settings.push_request_timeout_seconds
        )
    return NoPushSender()
