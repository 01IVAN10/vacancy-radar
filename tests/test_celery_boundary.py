"""Collection and delivery do not depend on Celery (see CLAUDE.md).

Only the worker and the thin task wrappers may import it; adapters, retry
policies and everything else are plain functions the CLI can run without it.
"""

import ast
from pathlib import Path

import vacancy_radar

PACKAGE = Path(vacancy_radar.__file__).parent


def imports_celery(path: Path) -> bool:
    """Whether the module imports `celery` anywhere, including inside functions."""
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        if any(name == "celery" or name.startswith("celery.") for name in names):
            return True
    return False


def test_only_the_worker_and_task_modules_import_celery() -> None:
    importers = {
        path.relative_to(PACKAGE).as_posix()
        for path in PACKAGE.rglob("*.py")
        if imports_celery(path)
    }

    assert importers, "the scan found no Celery import at all; is PACKAGE right?"
    assert {p for p in importers if p != "worker.py" and Path(p).name != "tasks.py"} == set()
