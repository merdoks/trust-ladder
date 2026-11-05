"""Tool schemas: what the model reads."""

_EXAMPLE = {
    "type": "object",
    "description": "A concrete tool call, e.g. {\"tool\": \"terminal\", \"args\": {\"command\": \"git push --force\"}}.",
    "properties": {
        "tool": {"type": "string", "description": "Tool name, e.g. terminal, write_file, patch."},
        "args": {"type": "object", "description": "The tool arguments, exactly as the call would send them."},
    },
    "required": ["tool", "args"],
}

LEARN = {
    "name": "trust_ladder_learn",
    "description": (
        "Turn a correction from the user into an ENFORCED guard so the same mistake cannot happen again. "
        "Call this right after the user corrects you (\"don't do X\", \"never run Y\", \"use A instead of B\", "
        "\"ask me before Z\"). Pass the correction in the user's words as `rule`, and whenever the mistake "
        "is visible in a tool call, also pass `guard` plus `bad_example` (the mistaken call) and, if there is "
        "one, `good_example` (the corrected call). trust-ladder installs the highest rung it can prove: "
        "1 guard (blocks matching tool calls before they run) > 2 check (verifies writes after they run) > "
        "3 rule (reminded every turn) > 4 review (asks the user first). A guard is only installed if it "
        "catches bad_example and lets good_example through; otherwise the rule falls back to a lower rung and "
        "the result says why. You can add rules but never remove them; only the user can (/trust)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "rule": {"type": "string", "description": "The correction in the user's own words (max 500 chars)."},
            "guard": {
                "type": "object",
                "description": "Rung 1: a deterministic matcher evaluated before every tool call.",
                "properties": {
                    "tool": {"type": "string", "description": "Tool to guard (terminal, write_file, patch, ...) or '*' for any tool."},
                    "kind": {"type": "string", "enum": ["regex", "path_glob", "tool"],
                             "description": "regex over an argument; path_glob over file paths (also catches rm/mv/cp/> in terminal); tool = block the tool entirely."},
                    "field": {"type": "string", "description": "Argument to match: command (terminal/execute_code), path, any, or a specific argument name."},
                    "pattern": {"type": "string", "description": "Python regex (kind=regex) or glob such as migrations/** or .env (kind=path_glob)."},
                    "unless": {"type": "string", "description": "Optional regex of allowed exceptions, e.g. --force-with-lease."},
                    "ignore_case": {"type": "boolean"},
                },
                "required": ["kind"],
            },
            "check": {
                "type": "object",
                "description": "Rung 2: a check on what a write puts into matching files (or on a tool's output).",
                "properties": {
                    "path_glob": {"type": "string", "description": "Files the check applies to, e.g. src/**/*.ts"},
                    "must_not_match": {"type": "string", "description": "Regex that must not appear in written content."},
                    "must_match": {"type": "string", "description": "Regex a full write_file must contain (e.g. a license header)."},
                    "result_must_not_match": {"type": "string", "description": "Regex that must not appear in the tool's output."},
                    "tool": {"type": "string", "description": "Restrict to one tool (optional)."},
                },
            },
            "review": {"type": "boolean", "description": "True when the user wants to be asked before matching calls (rung 4) instead of a hard block."},
            "fix": {"type": "string", "description": "What to do instead, shown when the guard fires (e.g. 'use git push --force-with-lease')."},
            "bad_example": _EXAMPLE,
            "good_example": _EXAMPLE,
        },
        "required": ["rule"],
    },
}

TASK = {
    "name": "trust_ladder_task",
    "description": (
        "Verified tasks: attach an objective verifier command (tests, a build, a lint, a curl health check) to a "
        "task, and only call the task done after `verify` passes (exit code 0). The verifier is fixed once "
        "created; you cannot edit or delete it, and trust-ladder flags any 'done' reply while a task in this "
        "session is unverified. Actions: create (name + verifier), verify (runs the verifier through Hermes's "
        "terminal tool, so normal approvals apply; a verifier that one of the user's guards blocks is refused), list."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "verify", "list"]},
            "task": {"type": "string", "description": "Task name or id (create: a short name; verify: the name or id)."},
            "verifier": {"type": "string", "description": "create only: the shell command that proves the task is done."},
            "timeout": {"type": "integer", "description": "verify only: seconds before the verifier is treated as failed (default 300, max 600)."},
        },
        "required": ["action"],
    },
}
