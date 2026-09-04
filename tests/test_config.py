from pathlib import Path

import pytest

from job_page_finder import create_deepseek_llm, load_environment


def test_creates_deepseek_client_from_env_file(tmp_path, monkeypatch) -> None:
    """Create a DeepSeek client using values loaded from an env file."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEEPSEEK_API_KEY=test-key\nDEEPSEEK_MODEL=deepseek-test-model\nDEEPSEEK_BASE_URL=https://deepseek.invalid/v1\n"
    )
    for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    llm = create_deepseek_llm(env_file=env_file)

    assert llm.api_key == "test-key"
    assert llm.model == "deepseek-test-model"
    assert llm.base_url == "https://deepseek.invalid/v1"
    assert llm.temperature == 0


def test_process_environment_takes_precedence_over_env_file(tmp_path, monkeypatch) -> None:
    """Prefer process environment variables over values from an env file."""
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=file-key\n")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "process-key")

    llm = create_deepseek_llm(env_file=env_file)

    assert llm.api_key == "process-key"


def test_requires_deepseek_api_key(tmp_path, monkeypatch) -> None:
    """Raise an error when no DeepSeek API key is configured."""
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_MODEL=deepseek-chat\n")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        create_deepseek_llm(env_file=env_file)


def test_load_environment_rejects_missing_explicit_file(tmp_path) -> None:
    """Raise an error when an explicitly requested env file is missing."""
    missing_file = tmp_path / "missing.env"

    with pytest.raises(FileNotFoundError, match=str(missing_file)):
        load_environment(missing_file)


def test_env_file_is_ignored_by_git() -> None:
    """Ensure the env file is excluded from Git tracking."""
    gitignore = Path(__file__).resolve().parents[1] / ".gitignore"

    assert ".env" in gitignore.read_text().splitlines()
