"""A scripted stand-in for the LLM so the demo is reproducible offline.

It uses Hermes's own recording fake provider (tests/fakes/fake_llm_provider.py from a
Hermes source checkout), which speaks the OpenAI Chat Completions wire format. Hermes,
the agent loop, the terminal tool and trust-ladder are all real; only the model's
choices are scripted. Run:  HERMES_SRC=/path/to/hermes-agent python scripted_model.py PORTFILE
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.environ.get("HERMES_SRC", "."))

from tests.fakes.fake_llm_provider import FakeLLMServer, Text, ToolCall  # noqa: E402

LEARN_ARGS = {
    "rule": "Never push straight to main. Push a branch and open a PR.",
    "guard": {"tool": "terminal", "kind": "regex", "field": "command",
              "pattern": r"git\s+push\b[^;&|]*\s(origin\s+)?(HEAD:)?(main|master)(\s|$)"},
    "fix": "push a branch (git switch -c <name> && git push -u origin <name>) and open a PR",
    "bad_example": {"tool": "terminal", "args": {"command": "git push origin main"}},
    "good_example": {"tool": "terminal", "args": {"command": "git push -u origin fix/login"}},
}

TURNS = [
    # (keyword in the user's message, tool calls to make in order, final answer)
    ("follow-up", [("terminal", {"command": "git push origin main"}),
                   ("terminal", {"command": "git switch -c fix/follow-up && git push -u origin fix/follow-up"})],
     "Your rule r1 blocked the push to main, so I pushed branch fix/follow-up instead. Ready for a PR."),
    ("Never push straight to main", [("trust_ladder_learn", LEARN_ARGS)],
     "Understood. I compiled that into guard r1: pushes to main are now blocked before they run; "
     "branch pushes still work."),
    ("Push my fix", [("terminal", {"command": "git push origin main"})],
     "Pushed your fix to origin/main."),
]


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def respond(record):
    body = record["body"]
    msgs = body.get("messages") or []
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
    user_text = _text(msgs[last_user].get("content")) if last_user >= 0 else ""
    done = sum(1 for m in msgs[last_user + 1:] if m.get("role") == "tool")
    offered = {str((t.get("function") or {}).get("name")) for t in body.get("tools") or []}
    for keyword, calls, final in TURNS:
        if keyword.lower() in user_text.lower():
            if done >= len(calls):
                return Text(final)
            name, args = calls[done]
            if name in offered or "tool_call" not in offered:
                return ToolCall(name, args)
            return ToolCall("tool_call", {"calls": [{"name": name, "arguments": args}]})
    return Text("OK.")


def main() -> None:
    port_file = Path(sys.argv[1])
    srv = FakeLLMServer(respond, record_get=True)
    srv.start()
    port_file.write_text(srv.base_url)
    seen = 0
    try:
        while True:
            time.sleep(0.5)
            for req in srv.requests[seen:]:
                print(req.get("kind"), req.get("path"), req.get("response", ""), flush=True)
            seen = len(srv.requests)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()


if __name__ == "__main__":
    main()
