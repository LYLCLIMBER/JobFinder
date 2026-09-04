import os
from pathlib import Path

from browser_use import ChatDeepSeek
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_environment(env_file: str | Path | None = None) -> Path | None:
    """Load the nearest supported .env file without overriding process variables."""
    if env_file is not None:
        path = Path(env_file).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Environment file does not exist: {path}")
        load_dotenv(path, override=False)
        return path

    for path in (PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"):
        if path.is_file():
            load_dotenv(path, override=False)
            return path
    return None


def create_deepseek_llm(
    *,
    env_file: str | Path | None = None,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 30,
) -> ChatDeepSeek:
    """Create a DeepSeek client from explicit values or .env configuration."""
    load_environment(env_file)

    resolved_api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
    if not resolved_api_key or not resolved_api_key.strip():
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    return ChatDeepSeek(
        api_key=resolved_api_key.strip(),
        model=model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        base_url=base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        timeout=timeout,
        temperature=0,
    )
