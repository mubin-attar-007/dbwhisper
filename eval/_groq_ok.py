"""Exit 0 if Groq/qwen3-32b answers right now, 1 if the intermittent 403 block is active.
Used by the wait-then-fix poller so the re-run fires the moment access recovers."""
import os
import pathlib
import sys

_ENV = pathlib.Path(__file__).parent.parent / ".env"
for line in _ENV.read_text(encoding="utf-8").splitlines():
    if line.strip() and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

try:
    from langchain_groq import ChatGroq
    ChatGroq(model="qwen/qwen3-32b", temperature=0, max_retries=0, max_tokens=64).invoke("Return: SELECT 1;")
    sys.exit(0)
except SystemExit:
    raise
except Exception:
    sys.exit(1)
