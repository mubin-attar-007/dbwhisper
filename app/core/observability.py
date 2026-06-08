"""Optional Sentry error tracking.

A no-op unless ``SENTRY_DSN`` is configured, so local/dev and unconfigured deploys are
unaffected. PII is disabled; combined with the log sanitizer this keeps query text and
secrets out of Sentry events.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.utils.logger import setup_logging

logger = setup_logging(__name__)


def init_sentry() -> None:
    """Initialize Sentry if a DSN is set; otherwise do nothing."""
    settings = get_settings()
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - sentry-sdk is a base dep, but stay defensive
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; skipping.")
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=0.1,
        send_default_pii=False,
    )
    logger.info("Sentry initialized (env=%s)", settings.app_env)
