# Structured logging
import contextlib
import json
import logging
import os
import time
from datetime import datetime

# --- Setup Log Directory ---
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = os.path.join(str(PROJECT_ROOT), "Log")
os.makedirs(LOG_DIR, exist_ok=True)


def get_daily_log_path() -> str:
    """Generate a log file path with the current date"""
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(LOG_DIR, f"app_{today}.log")


class _JsonFormatter(logging.Formatter):
    """Compact JSON log lines for production (parseable by log aggregators)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "line": record.lineno,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


# Read straight from env (this module is imported by app.core.config, so importing settings
# here would create a circular import).
def _resolve_level() -> int:
    return getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)


def _use_json() -> bool:
    raw = os.getenv("LOG_JSON")
    if raw is not None:
        return raw.strip().lower() not in ("0", "false", "no", "")
    return os.getenv("APP_ENV", "development").strip().lower() in ("production", "prod")


def _console_formatter() -> logging.Formatter:
    if _use_json():
        return _JsonFormatter()
    return logging.Formatter(
        "%(asctime)s - %(module)s:%(lineno)d - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S"
    )


def setup_logging(name: str = __name__) -> logging.Logger:
    """Setup structured logging with daily rotation and noise suppression."""
    log_path = get_daily_log_path()

    # Create a project-specific logger
    logger = logging.getLogger(name)
    logger.setLevel(_resolve_level())
    logger.propagate = False  # prevent bubbling to root logger

    # Clear old handlers
    if logger.handlers:
        for h in logger.handlers[:]:
            logger.removeHandler(h)

    # File handler
    try:
        file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(module)s:%(lineno)d - %(levelname)s - %(message)s",
                "%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(file_handler)
    except Exception:
        pass

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(_console_formatter())
    logger.addHandler(console_handler)

    # Add a sanitization filter to ensure we never log raw secrets accidentally.
    # Toggle via environment variable LOG_SANITIZE (1 or true = enable; 0 or false = disable)
    sanitize_enabled = os.getenv("LOG_SANITIZE", "1").lower() not in ("0", "false", "no")

    class SanitizingFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            """Sanitize string args in a logging record, but preserve numeric types.

            Avoid converting numbers (int/float/bool) into strings so that existing
            formatters using %d or other numeric placeholders continue to work.
            """
            try:
                if hasattr(record, "args") and record.args:
                    from app.utils.logger import sanitize_for_log as _sanitize

                    def _sanitize_arg(a):
                        # Keep numeric/bool types unchanged so %d remains valid
                        if isinstance(a, (int, float, bool)):
                            return a
                        if a is None:
                            return None
                        # For strings, sanitize directly
                        if isinstance(a, str):
                            return _sanitize(a)
                        # For other types, coerce to string then sanitize
                        try:
                            return _sanitize(str(a))
                        except Exception:
                            return "<unserializable>"

                    if isinstance(record.args, dict):
                        record.args = {k: _sanitize_arg(v) for k, v in record.args.items()}
                    elif isinstance(record.args, tuple):
                        record.args = tuple(_sanitize_arg(a) for a in record.args)
            except Exception:
                # Do not block logging on sanitizer errors
                pass
            return True

    # Attach the filter to the logger
    if sanitize_enabled:
        with contextlib.suppress(Exception):
            logger.addFilter(SanitizingFilter())

    # Suppress noisy third-party logs globally
    noisy_libs = [
        "asyncio",
        "urllib3",
        "matplotlib",
        "PIL",
        "tensorflow",
        "torch",
        "numba",
        "ultralytics",
        "cv2",
    ]
    for lib in noisy_libs:
        logging.getLogger(lib).setLevel(logging.WARNING)

    return logger


def _mask_password_in_connection_string(val: str) -> str:
    """Mask obvious passwords in connection strings or ODBC strings.

    This attempts several common patterns and replaces any found password
    value with '***'. It is best-effort and should not be relied on for
    security (keep secrets out of logs) but it reduces accidental leakage.
    """
    if not val or not isinstance(val, str):
        return val
    val.lower()
    # Simple URI form: scheme://user:pass@host/
    try:
        # mask user:password@host
        import re

        val = re.sub(r"(?P<pre>://[^:/@\s]+:)[^@\s]+@", r"\g<pre>***@", val)
        # mask user=...;pwd=...; or uid=...;password=...;
        val = re.sub(r"(?i)(password|pwd)=([^;\s]+)", r"\1=***", val)
        val = re.sub(r"(?i)(uid|user)=([^;\s]+)", r"\1=***", val)
    except Exception:
        pass
    return val


def _mask_sql_literals(sql: str) -> str:
    """Remove quoted string literals and numeric literals from a SQL/command text.

    This prevents accidental logging of PII that may appear in query constants.
    """
    if not sql or not isinstance(sql, str):
        return sql
    import re

    # replace quoted strings with <REDACTED>
    masked = re.sub(r"'[^']*'", "'<REDACTED>'", sql)
    masked = re.sub(r'"[^"]*"', '"<REDACTED>"', masked)
    # mask numeric tokens
    masked = re.sub(r"\b\d{3,}\b", "<NUM>", masked)
    # Truncate extremely long queries
    if len(masked) > 300:
        masked = masked[:300] + "..."
    return masked


def sanitize_for_log(value, *, max_len: int | None = 500) -> str:
    """Sanitize various types of values for safe logging.

    - Masks connection strings and API-like keys
    - Masks SQL literal values
    - Truncates long strings
    - Returns a printable representation for other types
    """
    if value is None:
        return "None"
    if isinstance(value, str):
        v = value.strip()
        v = _mask_password_in_connection_string(v)
        v = _mask_sql_literals(v)
        # mask common API key patterns (k=v or key: k)
        import re

        v = re.sub(r"(?i)(api_key|apikey|token|secret)=([\w-]{8,})", r"\1=***", v)
        v = re.sub(r"(?i)(bearer)\s+[A-Za-z0-9\-._~+/=]{8,}", r"\1 ***", v)
        if max_len and len(v) > max_len:
            v = v[:max_len] + "..."
        return v
    # For lists/dicts/others, produce a safe string summary
    try:
        import json

        if isinstance(value, (list, dict)):
            s = json.dumps(value, default=str)
            return sanitize_for_log(s, max_len=max_len)
        s = str(value)
        return sanitize_for_log(s, max_len=max_len)
    except Exception:
        return "<unserializable>"


def cleanup_old_logs(days_to_keep: int = 30):
    """Delete log files older than specified days."""
    now = time.time()
    for filename in os.listdir(LOG_DIR):
        if filename.startswith("app_") and filename.endswith(".log"):
            file_path = os.path.join(LOG_DIR, filename)
            try:
                if os.path.getmtime(file_path) < now - (days_to_keep * 86400):
                    os.remove(file_path)
                    logging.info(f"Deleted old log file: {filename}")
            except Exception:
                pass


# --- Example Usage ---
if __name__ == "__main__":
    logger = setup_logging(__name__)
    logger.debug("Debug log test — only visible if LOG_LEVEL=DEBUG.")
    logger.info("App started successfully.")
    cleanup_old_logs(30)
