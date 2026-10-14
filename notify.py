"""ntfy topic comes only from the NTFY_TOPIC repository secret (never stored in this public repo)."""
import os


def topic():
    return os.environ.get('NTFY_TOPIC', '').strip()
