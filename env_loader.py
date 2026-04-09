import os
import re
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


def validate_runtime_environment(project_root: str | None = None) -> dict:
    """Return startup validation results for env config and basic secret hygiene."""
    ensure_env_loaded()

    root = project_root or os.getcwd()
    errors: list[str] = []
    warnings: list[str] = []

    ollama_url = os.getenv("OLLAMA_URL", "").strip()
    if ollama_url and not ollama_url.startswith(("http://", "https://")):
        errors.append("OLLAMA_URL must start with http:// or https://")

    timeout_raw = os.getenv("OLLAMA_TIMEOUT_S", "240").strip()
    if timeout_raw:
        try:
            timeout_val = int(timeout_raw)
            if timeout_val < 20 or timeout_val > 1800:
                warnings.append("OLLAMA_TIMEOUT_S is outside recommended range (20..1800)")
        except Exception:
            errors.append("OLLAMA_TIMEOUT_S must be an integer")

    env_path = os.path.join(root, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                env_text = f.read()
            if "<" in env_text and ">" in env_text:
                warnings.append(".env seems to contain placeholder values (<...>)")
        except Exception:
            warnings.append("Unable to read .env for validation")

    secret_like = re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*=\s*[^\s]{10,}")
    scan_files = [
        os.path.join(root, ".env"),
        os.path.join(root, "README.md"),
    ]
    for path in scan_files:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            if secret_like.search(text):
                warnings.append(f"Potential hardcoded secret pattern found in {os.path.basename(path)}")
        except Exception:
            continue

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
    }
