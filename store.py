"""Local persistence for trust-ladder: rules, verified tasks and the catch ledger.

Everything lives in the plugin's own data directory
(``<HERMES_HOME>/plugin-data/trust-ladder/``):

* ``rules.json``   - the compiled rules (one canonical record per rule)
* ``tasks.json``   - verified tasks and their verifier commands
* ``ledger.jsonl`` - append-only log of catches (blocks, failed checks, reviews)

Writes are atomic (temp file + ``os.replace``) and serialised with a process
lock, so parallel tool calls in one agent cannot corrupt the files.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Iterator

try:  # POSIX advisory locks; Windows falls back to the in-process lock only
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

PLUGIN_NAME = "trust-ladder"
LEDGER_MAX_BYTES = 2 * 1024 * 1024  # trim the ledger past 2 MB
LEDGER_KEEP_ROWS = 5000

_LOCK = threading.RLock()
_HELD = threading.local()
VALID_RUNGS = ("guard", "check", "rule", "review")


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    """Serialise read-modify-write cycles across threads AND processes.

    The CLI, the gateway and a cron job can all run trust-ladder against the same profile at
    once; without a file lock two concurrent ``bump_catch`` calls could lose an update.
    Re-entrant within a thread.
    """
    with _LOCK:
        depth = getattr(_HELD, "depth", 0)
        if depth or fcntl is None:
            _HELD.depth = depth + 1
            try:
                yield
            finally:
                _HELD.depth = depth
            return
        fh = None
        try:
            fh = open(data_dir() / ".lock", "a+")
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            if fh is not None:
                fh.close()
            fh = None
        _HELD.depth = 1
        try:
            yield
        finally:
            _HELD.depth = 0
            if fh is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                fh.close()


def _valid_rule(rule: Any) -> bool:
    return (isinstance(rule, dict) and isinstance(rule.get("id"), str) and bool(rule["id"])
            and isinstance(rule.get("text"), str) and rule.get("rung") in VALID_RUNGS)


def data_dir() -> Path:
    """Return (and create) the plugin's data directory for the active profile."""
    try:
        from plugins.plugin_storage import plugin_data_dir

        return plugin_data_dir(PLUGIN_NAME)
    except Exception:  # pragma: no cover - very old Hermes without plugin_storage
        from hermes_constants import get_hermes_home

        root = get_hermes_home() / "plugin-data" / PLUGIN_NAME
        root.mkdir(parents=True, exist_ok=True)
        return root


def _read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        # Preserve a malformed file for the user instead of silently overwriting it.
        try:
            path.replace(path.with_suffix(path.suffix + f".corrupt-{int(time.time())}"))
        except OSError:
            pass
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def now() -> float:
    return time.time()


# --------------------------------------------------------------------------- rules


def load_rules() -> list[dict]:
    with _LOCK:
        data = _read_json(data_dir() / "rules.json", {"version": 1, "rules": []})
        rules = data.get("rules") if isinstance(data, dict) else None
        return [r for r in (rules or []) if _valid_rule(r)] if isinstance(rules, list) else []


def _highest(items: Iterable[dict], prefix: str) -> int:
    highest = 0
    for item in items:
        iid = str(item.get("id", ""))
        if iid.startswith(prefix) and iid[1:].isdigit():
            highest = max(highest, int(iid[1:]))
    return highest


def _stored_counter(path: Path) -> int:
    """The persisted next-id counter, so a removed rule's id is never handed out again."""
    data = _read_json(path, {}) if path.exists() else {}
    try:
        return int(data.get("next_id", 1)) if isinstance(data, dict) else 1
    except (TypeError, ValueError):
        return 1


def save_rules(rules: list[dict]) -> None:
    with _locked():
        path = data_dir() / "rules.json"
        counter = max(_stored_counter(path), _highest(rules, "r") + 1)
        _write_json(path, {"version": 1, "next_id": counter, "rules": rules})


def next_rule_id(rules: Iterable[dict]) -> str:
    """Never reuse an id (a removed r3 stays retired), so ledger history keeps its meaning."""
    counter = _stored_counter(data_dir() / "rules.json")
    return f"r{max(counter, _highest(rules, 'r') + 1)}"


def add_rule(rule: dict) -> dict:
    with _locked():
        rules = load_rules()
        # One canonical record per rule: identical text + matcher updates in place.
        for existing in rules:
            if existing.get("text", "").strip().lower() == rule.get("text", "").strip().lower() and \
                    existing.get("match") == rule.get("match") and existing.get("rung") == rule.get("rung"):
                existing["enabled"] = True
                existing["updated"] = now()
                save_rules(rules)
                return existing
        rule = dict(rule)
        rule.setdefault("id", next_rule_id(rules))
        rule.setdefault("created", now())
        rule.setdefault("enabled", True)
        rule.setdefault("catches", 0)
        rules.append(rule)
        save_rules(rules)
        return rule


def update_rule(rule_id: str, **changes: Any) -> dict | None:
    with _locked():
        rules = load_rules()
        for rule in rules:
            if rule.get("id") == rule_id:
                rule.update(changes)
                rule["updated"] = now()
                save_rules(rules)
                return rule
        return None


def remove_rule(rule_id: str) -> bool:
    with _locked():
        rules = load_rules()
        kept = [r for r in rules if r.get("id") != rule_id]
        if len(kept) == len(rules):
            return False
        save_rules(kept)
        return True


def bump_catch(rule_id: str) -> None:
    with _locked():
        rules = load_rules()
        for rule in rules:
            if rule.get("id") == rule_id:
                rule["catches"] = int(rule.get("catches", 0)) + 1
                rule["last_caught"] = now()
                save_rules(rules)
                return


# --------------------------------------------------------------------------- tasks


def load_tasks() -> list[dict]:
    with _LOCK:
        data = _read_json(data_dir() / "tasks.json", {"version": 1, "tasks": []})
        tasks = data.get("tasks") if isinstance(data, dict) else None
        if not isinstance(tasks, list):
            return []
        return [t for t in tasks if isinstance(t, dict) and isinstance(t.get("id"), str)
                and isinstance(t.get("name"), str) and isinstance(t.get("verifier"), str)]


def save_tasks(tasks: list[dict]) -> None:
    with _locked():
        path = data_dir() / "tasks.json"
        counter = max(_stored_counter(path), _highest(tasks, "t") + 1)
        _write_json(path, {"version": 1, "next_id": counter, "tasks": tasks})


def next_task_id(tasks: Iterable[dict]) -> str:
    counter = _stored_counter(data_dir() / "tasks.json")
    return f"t{max(counter, _highest(tasks, 't') + 1)}"


def record_verifier_result(task_id: str, verifier: str, result: dict) -> tuple[dict | None, bool]:
    """Write a verifier run's result onto the task as it is NOW, under the lock.

    Returns ``(task, counted)``. ``counted`` is False when the task's verifier changed while the
    run was in flight: the stale run never marks it passed and never restores the old verifier.
    """
    with _locked():
        tasks = load_tasks()
        for task in tasks:
            if task.get("id") != task_id:
                continue
            counted = task.get("verifier") == verifier
            task["attempts"] = int(task.get("attempts", 0)) + 1
            task["last_run"] = result.get("last_run")
            if counted:
                task.update(result)
            task["updated"] = now()
            save_tasks(tasks)
            return task, counted
        return None, False


def upsert_task(task: dict) -> dict:
    with _locked():
        tasks = load_tasks()
        for existing in tasks:
            if existing.get("id") == task.get("id"):
                existing.update(task)
                existing["updated"] = now()
                save_tasks(tasks)
                return existing
        task = dict(task)
        task.setdefault("id", next_task_id(tasks))
        task.setdefault("created", now())
        tasks.append(task)
        save_tasks(tasks)
        return task


def find_task(ref: str) -> dict | None:
    ref = (ref or "").strip().lower()
    if not ref:
        return None
    for task in load_tasks():
        if task.get("id", "").lower() == ref or task.get("name", "").lower() == ref:
            return task
    return None


def remove_task(ref: str) -> bool:
    with _locked():
        task = find_task(ref)
        if task is None:
            return False
        save_tasks([t for t in load_tasks() if t.get("id") != task["id"]])
    return True


# --------------------------------------------------------------------------- ledger


def append_ledger(event: dict) -> None:
    event = dict(event)
    event.setdefault("ts", now())
    path = data_dir() / "ledger.jsonl"
    line = json.dumps(event, ensure_ascii=False, default=str)
    with _locked():
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        try:
            if path.stat().st_size > LEDGER_MAX_BYTES:
                rows = path.read_text(encoding="utf-8").splitlines()[-LEDGER_KEEP_ROWS:]
                _atomic_text(path, "\n".join(rows) + "\n")
        except OSError:
            pass


def _atomic_text(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_ledger(limit: int | None = None) -> list[dict]:
    path = data_dir() / "ledger.jsonl"
    with _LOCK:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
    events = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events[-limit:] if limit else events
