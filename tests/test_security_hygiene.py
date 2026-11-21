"""Security and hygiene tests for AEROPATH (Sprint 1 - S1)."""

from pathlib import Path

from aeropath.config import Settings


def test_env_file_is_gitignored() -> None:
    gitignore_path = Path(".gitignore")
    assert gitignore_path.exists(), ".gitignore must exist"
    content = gitignore_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines()]
    assert ".env" in lines or any(line == ".env" for line in lines)
    assert "!.env.example" in lines


def test_env_example_has_placeholders_only() -> None:
    example_path = Path(".env.example")
    assert example_path.exists(), ".env.example must exist"
    content = example_path.read_text(encoding="utf-8")
    assert "aqi_PHf" not in content, "Real XKDR key found in .env.example"
    assert "AIza" not in content, "Real Google API key found in .env.example"


def test_cors_origins_defaults_to_empty_list() -> None:
    settings = Settings()
    # Origins cannot be ["*"] when allow_credentials=True
    assert settings.cors_origins == []


def test_secret_scanning_configs_exist() -> None:
    precommit_path = Path(".pre-commit-config.yaml")
    assert precommit_path.exists(), ".pre-commit-config.yaml must exist"
    precommit_text = precommit_path.read_text(encoding="utf-8")
    assert "gitleaks" in precommit_text.lower()

    ci_workflow_path = Path(".github/workflows/ci.yml")
    assert ci_workflow_path.exists(), ".github/workflows/ci.yml must exist"
    ci_text = ci_workflow_path.read_text(encoding="utf-8")
    assert "gitleaks" in ci_text.lower() or "secret" in ci_text.lower()
