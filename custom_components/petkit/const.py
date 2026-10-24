"""Constants for the PETKIT integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "petkit"
NAME = "PETKIT"
BASE_URL = "https://api.petkit.cn/6/"
APP_VERSION = "14.1.0"
APP_SOURCE = "app.petkit-android"
ANDROID_CLIENT_MODEL = "sdk_gphone64_arm64"
ANDROID_CLIENT_BRAND = "google"
ANDROID_CLIENT_OS_VERSION = "13"

CONF_ACCOUNT = "account"
CONF_PASSWORD = "password"
CONF_PASSWORD_HASH = "password_hash"
CONF_MEDIA_SIDECAR_URL = "media_sidecar_url"
CONF_MEDIA_SIDECAR_TOKEN = "media_sidecar_token"
PENDING_MEDIA_SIDECAR = "pending_media_sidecar"

SCAN_INTERVAL = timedelta(seconds=30)
PLAN_REFRESH_INTERVAL = timedelta(minutes=5)
EATING_POLL_INTERVAL = timedelta(seconds=4)
SESSION_REFRESH_MARGIN = 300

AUTH_ERROR_CODES = {5, 6, 1203}
VERIFICATION_REQUIRED_CODES = {157}

D4SH_TYPE_ID = 25
D4SH_MODEL = "D4sh"

CAMERA_APP_ID = "244c49951296440cbc1e3b937bf5e410"
CAMERA_RTC_SDK_VERSION = "4.24.0"
CAMERA_RTM_SDK_VERSION = "2.3.0"

STATIC_URL_PATH = "/petkit_static"
STATIC_REGISTERED = f"{DOMAIN}_static_registered"

PLATFORMS = ("binary_sensor", "button", "event", "sensor", "switch")

SERVICE_FEED_NOW = "feed_now"
SERVICE_SAVE_SCHEDULE = "save_schedule"
SERVICE_REFRESH_SCHEDULE = "refresh_schedule"
SERVICE_GET_CAMERA_ACCESS = "get_camera_access"
SERVICE_START_CAMERA_STREAM = "start_camera_stream"
SERVICE_STOP_CAMERA_STREAM = "stop_camera_stream"

ATTR_DEVICE_ID = "device_id"
ATTR_SESSION_ID = "session_id"
ATTR_AMOUNT1 = "amount1"
ATTR_AMOUNT2 = "amount2"
ATTR_DAYS = "days"
ATTR_EXPECTED_HASH = "expected_hash"
ATTR_QUALITY = "quality"

FOOD_STATUS_OPTIONS = ("empty", "low", "normal")
FOOD_STATUS_MAP = {
    0: "empty",
    1: "low",
    2: "normal",
}
