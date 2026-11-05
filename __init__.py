"""trust-ladder: turn a user's corrections into enforced guards.

Correct the agent once; trust-ladder compiles the correction to the highest rung
it can prove (guard > check > rule > human review), blocks the repeat at the
tool-call boundary, and keeps a ledger of every catch. See README.md.
"""

from __future__ import annotations

import logging

from . import schemas
from .ladder import TrustLadder

logger = logging.getLogger(__name__)

__all__ = ["register"]


def _setup_cli(subparser) -> None:
    subs = subparser.add_subparsers(dest="trust_ladder_command")
    subs.add_parser("status", help="Show rules by rung, verified tasks and recent catches")
    log = subs.add_parser("log", help="Show the catch ledger")
    log.add_argument("n", nargs="?", type=int, default=20)
    teach = subs.add_parser("teach", help="Teach a correction, e.g. teach \"never force-push\"")
    teach.add_argument("text", nargs="+")
    for name in ("disable", "enable", "remove"):
        p = subs.add_parser(name, help=f"{name.capitalize()} a rule by id")
        p.add_argument("rule_id")
    test = subs.add_parser("test", help="Dry-run a tool call against the rules: test terminal 'git push -f'")
    test.add_argument("tool")
    test.add_argument("args", nargs="*")


def register(ctx) -> None:
    """Hermes entry point."""
    ladder = TrustLadder(ctx)

    ctx.register_tool(name="trust_ladder_learn", toolset="trust_ladder", schema=schemas.LEARN,
                      handler=ladder.tool_learn, emoji="🪜",
                      description="Turn a user correction into an enforced guard")
    ctx.register_tool(name="trust_ladder_task", toolset="trust_ladder", schema=schemas.TASK,
                      handler=ladder.tool_task, emoji="✅",
                      description="Verified tasks: done only when the verifier passes")

    ctx.register_hook("pre_tool_call", ladder.on_pre_tool_call)
    ctx.register_hook("transform_tool_result", ladder.on_transform_tool_result)
    ctx.register_hook("pre_llm_call", ladder.on_pre_llm_call)
    ctx.register_hook("pre_verify", ladder.on_pre_verify)
    ctx.register_hook("transform_llm_output", ladder.on_transform_llm_output)

    def _teach(raw_args: str = "", **kwargs) -> str:
        return ladder.cmd_teach(raw_args, session_id=str(kwargs.get("session_id") or ""))

    def _trust(raw_args: str = "", **kwargs) -> str:
        return ladder.cmd_trust(raw_args)

    def _verify(raw_args: str = "", **kwargs) -> str:
        return ladder.cmd_verify(raw_args, session_id=str(kwargs.get("session_id") or ""))

    ctx.register_command("teach", handler=_teach, args_hint="<correction>",
                         description="Teach a correction; trust-ladder compiles it into a guard")
    ctx.register_command("trust", handler=_trust, args_hint="[status|log|disable <id>|enable <id>|test ...]",
                         description="Show trust-ladder rules by rung and their catches")
    ctx.register_command("verify", handler=_verify, args_hint="<name> -- <command> | list | run <name>",
                         description="Attach a verifier command to a task")

    def _cli(args) -> None:
        sub = getattr(args, "trust_ladder_command", None) or "status"
        if sub == "status":
            print(ladder.render_status())
        elif sub == "log":
            print(ladder.render_log(args.n))
        elif sub == "teach":
            print(ladder.cmd_teach(" ".join(args.text), session_id="cli"))
        elif sub in ("disable", "enable", "remove"):
            print(ladder.cmd_trust(f"{sub} {args.rule_id}"))
        elif sub == "test":
            print(ladder.cmd_test([args.tool, *args.args]))

    ctx.register_cli_command(name="trust-ladder", help="Inspect and teach trust-ladder rules",
                             setup_fn=_setup_cli, handler_fn=_cli,
                             description="Rules by rung, the catch ledger, and dry-run tests")
