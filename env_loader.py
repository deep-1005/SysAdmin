import os
from dotenv import load_dotenv, find_dotenv

_ENV_LOADED = False


def ensure_env_loaded() -> None:
    """Load .env once for the current process."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return

    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path, override=False)
    else:
        load_dotenv(override=False)

    _ENV_LOADED = True
