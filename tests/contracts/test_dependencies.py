from ast import Import, ImportFrom, parse, walk
from pathlib import Path

CORE_MODULES = {
    "application.py",
    "completion.py",
    "contracts.py",
    "core_models.py",
    "finder.py",
    "ports.py",
    "task_protocol.py",
}
TEMPORARY_IMPORT_EXCEPTIONS: dict[str, str] = {}


def test_core_modules_do_not_import_browser_use() -> None:
    package = Path(__file__).parents[2] / "src" / "job_page_finder"
    violations: dict[str, list[str]] = {}

    for name in sorted(CORE_MODULES):
        path = package / name
        if not path.exists() or name in TEMPORARY_IMPORT_EXCEPTIONS:
            continue
        tree = parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = []
        for node in walk(tree):
            if isinstance(node, Import):
                imported.extend(
                    alias.name
                    for alias in node.names
                    if alias.name == "browser_use" or alias.name.startswith("browser_use.")
                )
            elif isinstance(node, ImportFrom) and node.module:
                if node.module == "browser_use" or node.module.startswith("browser_use."):
                    imported.append(node.module)
        if imported:
            violations[name] = sorted(set(imported))

    assert not violations, f"core modules import browser_use: {violations}"
