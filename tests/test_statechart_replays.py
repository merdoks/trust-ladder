"""Bug paths found by TLC in docs/statechart (RuleLadder.tla, VerifiedTask.tla), replayed on the code.

Each test follows one counterexample step by step. Two processes share a profile in the first
three: the agent's (hooks) and the user's (/trust or `hermes trust-ladder` from another shell).
"""

import json
import os

from conftest import tl

store = tl.store
TrustLadder = tl.ladder.TrustLadder


def call(ctx, command):
    out = None
    for cb in ctx.hooks["pre_tool_call"]:
        out = cb(tool_name="terminal", args={"command": command}, task_id="t") or out
    return out


def same_clock_tick(write):
    """Run a write the way Linux stamps it inside one coarse clock tick: mtime_ns unchanged.

    (Measured on this machine: 198 of 200 back-to-back replaces kept the same mtime_ns.)
    """
    path = store.data_dir() / "rules.json"
    before = path.stat().st_mtime_ns
    write()
    os.utime(path, ns=(before, before))


def test_rule_ladder_path_active_guard_blocks(ctx):
    # path: ACallArrives -> YouTeach("guard") [same tick] -> ACallArrives
    user = TrustLadder(None)
    user.learn("keep commits small", source="teach")          # rules.json exists
    assert call(ctx, "git push -f origin x") is None           # agent loads its view
    same_clock_tick(lambda: user.learn("never force-push", source="teach"))
    res = call(ctx, "git push -f origin x")
    assert res and res["action"] == "block"                    # ActiveGuardBlocks


def test_rule_ladder_path_disabled_rule_never_blocks(ctx):
    # path: YouTeach("guard") -> ACallArrives -> YouDisable [same tick] -> ACallArrives
    user = TrustLadder(None)
    user.learn("never force-push", source="teach")
    assert call(ctx, "ls") is None                             # agent loads its view
    same_clock_tick(lambda: user.cmd_trust("disable r1"))
    assert call(ctx, "git push -f origin x") is None           # DisabledRuleNeverBlocks


def test_rule_ladder_path_rule_ids_never_reused(ctx):
    # path: YouTeach -> YouRemove -> YouTeach
    ctx.commands["teach"]("never force-push")
    ctx.commands["trust"]("remove r1")
    ctx.commands["teach"]("never run `make deploy`")
    rule = store.load_rules()[0]
    assert rule["id"] != "r1"                                  # RuleIdsNeverReused
    # so the ledger's old r1 catches are never shown under the new rule
    assert "make deploy" in ctx.commands["trust"]("")


def test_task_ids_never_reused(ctx):
    ctx.commands["verify"]("a -- true")
    ctx.commands["verify"]("drop a")
    ctx.commands["verify"]("b -- true")
    assert store.load_tasks()[0]["id"] != "t1"


def test_verified_task_path_user_verifier_never_reverted(ctx, ladder):
    # path: AgentCreatesTask -> VerifyStarts -> UserSetsRealVerifier -> VerifierExits(pass)
    h = ctx.tools["trust_ladder_task"]["handler"]
    h({"action": "create", "task": "ship", "verifier": "true"}, session_id="s")

    def slow_terminal(command, timeout, task_id=""):
        # while the agent's easy verifier runs, the user sets the real one
        ladder.cmd_verify("ship -- pytest -q", session_id="s")
        return json.dumps({"output": "ok", "exit_code": 0, "error": None})

    res = ladder.run_verifier("ship", session_id="s", dispatch=slow_terminal)
    task = store.find_task("ship")
    assert task["verifier"] == "pytest -q"                     # UserVerifierNeverReverted
    assert task["status"] != "passed"                          # PassedOnlyWithPassingVerifier
    assert res["passed"] is False
