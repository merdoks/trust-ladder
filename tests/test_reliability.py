"""Reliability pass: slow regexes, broken state, verifier bypass, secrets, unattended runs."""

import json
import threading
import time

import pytest

from conftest import tl

compiler, store, ladder_mod = tl.compiler, tl.store, tl.ladder


def fire(ctx, hook, **kw):
    out = None
    for cb in ctx.hooks.get(hook, []):
        res = cb(**kw)
        out = res if res is not None else out
    return out


LONG = ("git status && echo " + "x y z " * 4000)[:20_000]


# ---------------------------------------------------------------- regex cost / ReDoS


@pytest.mark.parametrize("pattern", [r"a.*b.*c.*x", r"\s*\s*\s*x", r"[a-z]*[a-z]*[a-z]*!"])
def test_catastrophic_patterns_are_refused_quickly(pattern):
    started = time.perf_counter()
    with pytest.raises(compiler.CompileError, match="too slow"):
        compiler.normalize_match({"tool": "terminal", "kind": "regex", "field": "command", "pattern": pattern})
    assert time.perf_counter() - started < 2.0  # vetting itself never stalls


def test_leading_dotstar_is_dropped():
    m = compiler.normalize_match({"tool": "terminal", "kind": "regex", "field": "command",
                                  "pattern": ".*git push.*main"})
    assert m["pattern"] == "git push.*main"


def test_natural_but_polynomial_pattern_gets_a_window_and_stays_fast():
    m = compiler.normalize_match({"tool": "terminal", "kind": "regex", "field": "command",
                                  "pattern": r"git.*push.*--force"})
    assert m.get("window", compiler.MAX_SUBJECT_LEN) < compiler.MAX_SUBJECT_LEN
    started = time.perf_counter()
    assert compiler.evaluate_match(m, "terminal", {"command": LONG}) is None
    assert time.perf_counter() - started < 1.0
    hit = compiler.evaluate_match(m, "terminal", {"command": LONG[:15000] + " ; git push --force origin x"})
    assert hit and "--force" in hit


def test_cheap_patterns_keep_full_length():
    m = compiler.normalize_match({"tool": "terminal", "kind": "regex", "field": "command",
                                  "pattern": r"curl[^|]*\|\s*(ba)?sh"})
    assert "window" not in m


def test_slash_regex_from_teach_is_vetted(ctx):
    reply = ctx.commands["teach"]("never run /a.*b.*c.*x/")
    assert "rung 3" in reply and "too slow" in reply


def test_slow_check_pattern_refused_falls_back(ladder):
    out = ladder.learn("no", source="model", check={"path_glob": "*.py", "must_not_match": r"\s*\s*\s*x"})
    assert out["rung"] == "rule" and any("too slow" in p for p in out["problems"])


def test_windowed_search_matches_across_window_overlap():
    import re
    rx = re.compile(r"needle")
    text = "a" * 1000 + "needle" + "b" * 1000
    hit, _ = compiler.windowed_search(rx, text, 600)
    assert hit and text[hit.start():hit.end()] == "needle"


# ---------------------------------------------------------------- broken state


def test_corrupt_rule_does_not_disable_other_guards(ctx, home):
    ctx.commands["teach"]("never force-push")
    rules = store.load_rules()
    raw = {"version": 1, "rules": [{"id": "r0", "rung": "guard"},  # no text
                                   {"id": "r9", "text": "bad", "rung": "guard",
                                    "match": {"tools": ["terminal"], "kind": "regex", "field": "command",
                                              "pattern": "("}},  # invalid regex
                                   *rules]}
    (store.data_dir() / "rules.json").write_text(json.dumps(raw))
    res = fire(ctx, "pre_tool_call", tool_name="terminal", args={"command": "git push -f origin x"}, task_id="t")
    assert res and res["action"] == "block"
    assert "r0" not in ctx.commands["trust"]("")


def test_malformed_rules_file_is_preserved_and_ignored(ctx, home):
    path = store.data_dir() / "rules.json"
    path.write_text("{not json")
    assert fire(ctx, "pre_tool_call", tool_name="terminal", args={"command": "ls"}, task_id="t") is None
    assert any(p.name.startswith("rules.json.corrupt-") for p in store.data_dir().iterdir())


def test_non_dict_args_and_odd_values(ctx):
    ctx.commands["teach"]("don't edit .env")
    for args in (None, [], "rm .env", {"path": None}, {"path": 5}, {"path": ["a", ".env"]}):
        fire(ctx, "pre_tool_call", tool_name="write_file", args=args, task_id="t")
    res = fire(ctx, "pre_tool_call", tool_name="write_file", args={"path": ["a", ".env"]}, task_id="t")
    assert res and res["action"] == "block"


def test_bad_config_values_do_not_break_injection(ctx):
    ctx.commands["teach"]("prefer small commits")
    ctx.config["max_injected_rules"] = "lots"
    out = fire(ctx, "pre_llm_call", session_id="s", user_message="hi")
    assert "prefer small commits" in out["context"]


def test_trust_test_never_raises(ctx):
    ctx.commands["teach"]("never force-push")
    assert "Usage" in ctx.commands["trust"]("test")
    assert "r1" in ctx.commands["trust"]("test terminal [1,2]") or "No rule" in ctx.commands["trust"]("test terminal [1,2]")
    assert "r1" in ctx.commands["trust"]("test execute_code git push -f origin x")


# ---------------------------------------------------------------- verified tasks cannot bypass guards


def test_model_verifier_cannot_run_a_guarded_command(ctx):
    ctx.commands["teach"]("never push straight to main")
    out = json.loads(ctx.tools["trust_ladder_task"]["handler"](
        {"action": "create", "task": "ship", "verifier": "git push origin main"}, session_id="s"))
    assert out["ok"] is False and "r1" in out["error"]
    assert ctx.dispatched == []


def test_guard_added_after_task_still_stops_verifier(ctx):
    h = ctx.tools["trust_ladder_task"]["handler"]
    json.loads(h({"action": "create", "task": "ship", "verifier": "git push origin main"}, session_id="s"))
    ctx.commands["teach"]("never push straight to main")
    out = json.loads(h({"action": "verify", "task": "ship"}, session_id="s"))
    assert out["ok"] is False and ctx.dispatched == []
    assert store.read_ledger()[-1]["type"] == "verify_refused"


def test_user_verifier_may_match_a_review_rule(ctx):
    ctx.commands["teach"]("ask me before running `terraform plan`")
    reply = ctx.commands["verify"]("plan -- terraform plan")
    assert "Verified task" in reply
    model = json.loads(ctx.tools["trust_ladder_task"]["handler"](
        {"action": "create", "task": "plan2", "verifier": "terraform plan -out x"}, session_id="s"))
    assert model["ok"] is False


def test_verifier_timeout_is_clamped_and_dispatch_errors_fail_cleanly(ctx):
    h = ctx.tools["trust_ladder_task"]["handler"]
    h({"action": "create", "task": "tests", "verifier": "pytest -q"}, session_id="s")
    h({"action": "verify", "task": "tests", "timeout": 99999}, session_id="s")
    assert ctx.dispatched[-1][1]["timeout"] == ladder_mod.MAX_VERIFY_TIMEOUT

    def boom(*a, **k):
        raise RuntimeError("terminal unavailable")
    ctx.dispatch_tool = boom
    out = json.loads(h({"action": "verify", "task": "tests"}, session_id="s"))
    assert out["ok"] and out["passed"] is False


# ---------------------------------------------------------------- secrets never land in the ledger


def test_ledger_and_notice_redact_secrets(ctx):
    ctx.commands["teach"]("never run `export OPENAI_API_KEY`")
    secret = "sk-proj-abcdefghijklmnop1234567890"
    fire(ctx, "pre_tool_call", tool_name="terminal",
         args={"command": f"export OPENAI_API_KEY={secret}"}, task_id="t")
    ledger = (store.data_dir() / "ledger.jsonl").read_text()
    assert secret not in ledger and secret not in "".join(ctx.posted)


def test_redact_keeps_ordinary_text():
    for text in ("git push origin main", "path src/components/a/b/c/d/e/f/g/h/file.tsx", "rm -rf build"):
        assert ladder_mod.redact(text) == text
    assert "hunter2hunter" not in ladder_mod.redact("password=hunter2hunter")


def test_plugin_reads_no_environment_or_processes():
    from pathlib import Path
    src = "".join(p.read_text() for p in Path(tl.__file__).parent.glob("*.py"))
    for forbidden in ("os.environ", "getenv(", "subprocess", "urllib", "requests", "socket", "Popen"):
        assert forbidden not in src, forbidden


# ---------------------------------------------------------------- concurrency


def test_concurrent_catches_are_not_lost(ctx):
    ctx.commands["teach"]("never force-push")
    ctx.config["notify_catches"] = False

    def worker():
        for _ in range(10):
            fire(ctx, "pre_tool_call", tool_name="terminal", args={"command": "git push -f o x"}, task_id="t")
    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert store.load_rules()[0]["catches"] == 50


def test_profile_switch_does_not_serve_stale_rules(ctx, tmp_path, monkeypatch, ladder):
    ctx.commands["teach"]("never force-push")
    assert ladder.rules()
    other = tmp_path / "other-profile"
    other.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(other))
    assert ladder.rules() == []


# ---------------------------------------------------------------- unattended: nothing waits


def test_cron_turn_never_blocks_on_people(ctx):
    ctx.cron = object()
    ctx.commands["teach"]("never force-push")
    ctx.commands["teach"]("ask me before running `terraform apply`")
    started = time.perf_counter()
    blocked = fire(ctx, "pre_tool_call", tool_name="terminal", args={"command": "git push -f o x"}, task_id="c")
    review = fire(ctx, "pre_tool_call", tool_name="terminal", args={"command": "terraform apply"}, task_id="c")
    assert blocked["action"] == "block" and review["action"] == "approve"
    assert time.perf_counter() - started < 1.0
