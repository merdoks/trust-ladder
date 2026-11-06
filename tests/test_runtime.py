import json

from conftest import tl

store = tl.store


def fire(ctx, hook, **kw):
    out = None
    for cb in ctx.hooks.get(hook, []):
        res = cb(**kw)
        out = res if res is not None else out
    return out


def test_manifest_matches_registration(ctx):
    from pathlib import Path
    text = (Path(tl.__file__).parent / "plugin.yaml").read_text()
    for name in ctx.tools:
        assert f"- {name}" in text
    for hook in ctx.hooks:
        assert f"- {hook}" in text
    assert set(ctx.commands) == {"teach", "trust", "verify"}
    assert "trust-ladder" in ctx.cli


def test_teach_then_block_then_status(ctx, home):
    reply = ctx.commands["teach"]("never force-push; use --force-with-lease", session_id="s1")
    assert "rung 1" in reply and "r1" in reply

    res = fire(ctx, "pre_tool_call", tool_name="terminal", args={"command": "git push --force origin main"},
               task_id="s2")
    assert res["action"] == "block"
    assert "r1" in res["message"] and "--force-with-lease" in res["message"]

    ok = fire(ctx, "pre_tool_call", tool_name="terminal",
              args={"command": "git push --force-with-lease origin main"}, task_id="s2")
    assert ok is None
    assert len(ctx.posted) == 1 and "trust-ladder blocked terminal" in ctx.posted[0]
    ctx.config["notify_catches"] = False
    fire(ctx, "pre_tool_call", tool_name="terminal", args={"command": "git push -f"}, task_id="s2")
    assert len(ctx.posted) == 1

    status = ctx.commands["trust"]("")
    assert "caught 2x" in status and "RECENT CATCHES" in status
    events = store.read_ledger()
    assert [e["type"] for e in events] == ["learned", "block", "block"]
    assert (home / "plugin-data" / "trust-ladder" / "rules.json").exists()


def test_model_learn_with_verified_guard(ctx):
    handler = ctx.tools["trust_ladder_learn"]["handler"]
    out = json.loads(handler({
        "rule": "Stop deleting the data directory",
        "guard": {"tool": "terminal", "kind": "regex", "field": "command", "pattern": r"rm\s+-\w*r\w*\s+\S*data/?\b"},
        "bad_example": {"tool": "terminal", "args": {"command": "rm -rf ./data"}},
        "good_example": {"tool": "terminal", "args": {"command": "rm -rf ./data/tmp-cache.json.bak"}},
        "fix": "delete only files inside data/tmp",
    }, task_id="s9"))
    # the good example also matches -> guard rejected, falls back (and says why)
    assert out["ok"] and out["problems"]
    out2 = json.loads(handler({
        "rule": "Stop deleting the data directory",
        "guard": {"tool": "terminal", "kind": "regex", "field": "command", "pattern": r"rm\s+-\w*r\w*\s+\S*data/?(\s|$)"},
        "bad_example": {"tool": "terminal", "args": {"command": "rm -rf ./data"}},
        "good_example": {"tool": "terminal", "args": {"command": "rm -rf ./data/tmp"}},
    }, task_id="s9"))
    assert out2["rung"].startswith("1") and not out2["problems"]
    res = fire(ctx, "pre_tool_call", tool_name="terminal", args={"command": "rm -rf data"}, task_id="s9")
    assert res and res["action"] == "block"


def test_guard_without_proof_falls_back(ctx):
    handler = ctx.tools["trust_ladder_learn"]["handler"]
    out = json.loads(handler({"rule": "be careful with migrations",
                              "guard": {"tool": "write_file", "kind": "path_glob", "pattern": "migrations/**"}}))
    assert out["ok"]
    assert out["rung"].startswith("3")  # no bad_example and the text has no matcher -> rule
    assert any("bad_example" in p for p in out["problems"])


def test_model_cannot_install_catch_all(ctx):
    handler = ctx.tools["trust_ladder_learn"]["handler"]
    out = json.loads(handler({"rule": "x", "guard": {"tool": "*", "kind": "regex", "pattern": ".*"},
                              "bad_example": {"tool": "terminal", "args": {"command": "ls"}}}))
    assert out["rung"].startswith("3")


def test_check_rung_appends_failure(ctx):
    ctx.commands["teach"]("src/**/*.py must not contain breakpoint()")
    result = json.dumps({"ok": True, "bytes": 20})
    out = fire(ctx, "transform_tool_result", tool_name="write_file",
               args={"path": "src/app.py", "content": "breakpoint()\n"}, result=result, task_id="s1")
    assert out.startswith(result) and "FAILED" in out and "r1" in out
    clean = fire(ctx, "transform_tool_result", tool_name="write_file",
                 args={"path": "src/app.py", "content": "pass\n"}, result=result, task_id="s1")
    assert clean is None
    failed_call = fire(ctx, "transform_tool_result", tool_name="write_file",
                       args={"path": "src/app.py", "content": "breakpoint()"}, result='{"error": "denied"}')
    assert failed_call is None


def test_review_rung_always_defers_to_hermes_gate(ctx):
    ctx.commands["teach"]("ask me before running `terraform apply`")
    res = fire(ctx, "pre_tool_call", tool_name="terminal", args={"command": "terraform apply -auto-approve"},
               task_id="s")
    assert res["action"] == "approve" and res["rule_key"] == "trust-ladder:r1"
    # In cron the decision still goes to Hermes's gate, which resolves it instantly from
    # approvals.cron_mode (default deny); trust-ladder never approves or waits itself.
    ctx.cron = object()
    res = fire(ctx, "pre_tool_call", tool_name="terminal", args={"command": "terraform apply"}, task_id="s")
    assert res["action"] == "approve"
    assert store.read_ledger()[-1]["type"] == "review_unattended"


def test_observe_mode_logs_without_blocking(ctx):
    ctx.commands["teach"]("never run `make deploy`")
    ctx.commands["trust"]("observe")
    assert fire(ctx, "pre_tool_call", tool_name="terminal", args={"command": "make deploy"}, task_id="s") is None
    assert store.read_ledger()[-1]["type"] == "observe"
    ctx.commands["trust"]("enforce")
    assert fire(ctx, "pre_tool_call", tool_name="terminal", args={"command": "make deploy"}, task_id="s")


def test_disable_enable_remove(ctx):
    ctx.commands["teach"]("never run `make deploy`")
    assert "Done" in ctx.commands["trust"]("disable r1")
    assert fire(ctx, "pre_tool_call", tool_name="terminal", args={"command": "make deploy"}, task_id="s") is None
    ctx.commands["trust"]("enable r1")
    assert fire(ctx, "pre_tool_call", tool_name="terminal", args={"command": "make deploy"}, task_id="s")
    ctx.commands["trust"]("remove r1")
    assert store.load_rules() == []
    assert "No such rule" in ctx.commands["trust"]("disable r9")


def test_duplicate_teach_is_one_record(ctx):
    ctx.commands["teach"]("never run `make deploy`")
    ctx.commands["teach"]("Never run `make deploy`")
    assert len(store.load_rules()) == 1


def test_pre_llm_injection(ctx):
    assert fire(ctx, "pre_llm_call", session_id="s", user_message="hello") is None
    ctx.commands["teach"]("Always write tests first")
    ctx.commands["teach"]("never force-push")
    out = fire(ctx, "pre_llm_call", session_id="s", user_message="please continue")["context"]
    assert "Always write tests first" in out and "[r2]" in out and "promote" in out
    nudge = fire(ctx, "pre_llm_call", session_id="s", user_message="No, don't do that again")["context"]
    assert "trust_ladder_learn" in nudge
    ctx.config["inject_rules"] = False
    ctx.config["correction_nudge"] = False
    assert fire(ctx, "pre_llm_call", session_id="s", user_message="no, stop") is None


def test_verified_task_flow(ctx):
    task_tool = ctx.tools["trust_ladder_task"]["handler"]
    made = json.loads(task_tool({"action": "create", "task": "tests", "verifier": "pytest -q"}, session_id="s1"))
    assert made["ok"]
    # model cannot swap the verifier for an easier one
    swap = json.loads(task_tool({"action": "create", "task": "tests", "verifier": "true"}, session_id="s1"))
    assert "fixed" in swap["error"]

    # done-claims are flagged while unverified
    flagged = fire(ctx, "transform_llm_output", response_text="All done, tests are fixed!", session_id="s1")
    assert "NOT VERIFIED" in flagged
    assert fire(ctx, "pre_verify", session_id="s1", attempt=0)["action"] == "continue"
    assert fire(ctx, "pre_verify", session_id="s1", attempt=2) is None
    assert "Verified tasks not yet passed" in fire(ctx, "pre_llm_call", session_id="s1", user_message="go")["context"]

    ctx.terminal_result = json.dumps({"output": "1 failed", "exit_code": 1, "error": None})
    failed = json.loads(task_tool({"action": "verify", "task": "tests"}, session_id="s1", task_id="s1"))
    assert failed["passed"] is False and failed["exit_code"] == 1
    assert ctx.dispatched[-1][0] == "terminal" and ctx.dispatched[-1][1]["command"] == "pytest -q"

    ctx.terminal_result = json.dumps({"output": "3 passed", "exit_code": 0, "error": None})
    passed = json.loads(task_tool({"action": "verify", "task": "t1"}, session_id="s1", task_id="s1"))
    assert passed["passed"] is True
    assert fire(ctx, "transform_llm_output", response_text="All done!", session_id="s1") is None
    assert fire(ctx, "pre_verify", session_id="s1", attempt=0) is None
    types = [e["type"] for e in store.read_ledger()]
    assert types[-2:] == ["verify_fail", "verify_pass"]


def test_verify_slash_command(ctx):
    msg = ctx.commands["verify"]("build -- make build", session_id="s1")
    assert "t1" in msg
    ctx.terminal_result = json.dumps({"output": "ok", "exit_code": 0})
    assert "PASSED" in ctx.commands["verify"]("run build", session_id="s1")
    assert "PASSED" in ctx.commands["trust"]("")
    assert "Dropped" in ctx.commands["verify"]("drop build")


def test_terminal_error_counts_as_failure(ctx):
    ctx.commands["verify"]("deploy-check -- curl -fsS localhost:8080/health")
    ctx.terminal_result = json.dumps({"output": "", "exit_code": -1, "error": "approval denied"})
    assert "FAILED" in ctx.commands["verify"]("run deploy-check")


def test_corrupt_rules_file_is_preserved(ctx, home):
    d = home / "plugin-data" / "trust-ladder"
    d.mkdir(parents=True, exist_ok=True)
    (d / "rules.json").write_text("{not json")
    assert store.load_rules() == []
    assert list(d.glob("rules.json.corrupt-*"))


def test_hooks_never_raise(ctx, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    ladder = ctx.tools["trust_ladder_learn"]["handler"].__self__
    monkeypatch.setattr(ladder, "rules", boom)
    assert fire(ctx, "pre_tool_call", tool_name="terminal", args={"command": "ls"}, task_id="s") is None
    assert fire(ctx, "transform_tool_result", tool_name="write_file", args={"path": "a"}, result="{}") is None
    assert fire(ctx, "pre_llm_call", session_id="s", user_message="hi") is None


def test_test_subcommand(ctx):
    ctx.commands["teach"]("never force-push")
    assert "would fire" in ctx.commands["trust"]("test terminal 'git push -f origin main'")
    assert "No rule fires" in ctx.commands["trust"]("test terminal 'git status'")


def test_cli_status(ctx, capsys):
    import argparse
    setup, handler = ctx.cli["trust-ladder"]
    parser = argparse.ArgumentParser()
    setup(parser)
    handler(parser.parse_args(["teach", "never", "force-push"]))
    handler(parser.parse_args(["status"]))
    out = capsys.readouterr().out
    assert "Learned r1" in out and "RUNG 1" in out


def test_ledger_trims(ctx, monkeypatch):
    monkeypatch.setattr(store, "LEDGER_MAX_BYTES", 2000)
    monkeypatch.setattr(store, "LEDGER_KEEP_ROWS", 10)
    for i in range(100):
        store.append_ledger({"type": "block", "rule": "r1", "evidence": "x" * 50})
    assert len(store.read_ledger()) <= 60


# Replays of what gpt-oss-20b actually sent on 2026-11-06 (sentence guard, string examples).
def test_weak_model_sentence_guard_and_string_example(ctx):
    handler = ctx.tools["trust_ladder_learn"]["handler"]
    out = json.loads(handler({
        "rule": "Never push directly to main; always push on a branch and open a PR",
        "guard": "Do not push directly to main; always push on a branch and open a PR",
        "bad_example": "git push origin main",
    }, task_id="w1"))
    assert out["ok"] and out["rung"].startswith("1"), out
    res = fire(ctx, "pre_tool_call", tool_name="terminal", args={"command": "git push origin main"}, task_id="w2")
    assert res and res["action"] == "block"
    assert fire(ctx, "pre_tool_call", tool_name="terminal",
                args={"command": "git push -u origin fix/x"}, task_id="w2") is None


def test_weak_model_no_rule_prose_example(ctx):
    handler = ctx.tools["trust_ladder_learn"]["handler"]
    out = json.loads(handler({
        "guard": "Do not push directly to main; always push on a branch and open a PR",
        "bad_example": "Pushed code directly to main and committed changes without creating a branch or PR",
    }, task_id="w3"))
    assert out["ok"] and out["rung"].startswith("1"), out
    assert any("description" in p for p in out["problems"])


def test_string_example_that_guard_misses_still_downgrades(ctx):
    handler = ctx.tools["trust_ladder_learn"]["handler"]
    out = json.loads(handler({"rule": "never push straight to main", "bad_example": "git status"}))
    assert out["ok"] and out["rung"].startswith("3")
