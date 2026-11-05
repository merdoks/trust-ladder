"""Compile a correction into the highest rung of the trust ladder it can reach.

The ladder (from @poteto's "fix ladder", best first):

1. ``guard``  - make the mistake impossible: a deterministic matcher evaluated in
   ``pre_tool_call`` that BLOCKS the tool call before it runs.
2. ``check``  - an automated check evaluated after a write/tool call; a failure is
   appended to the tool result so the model fixes it immediately.
3. ``rule``   - plain guidance re-injected every turn via ``pre_llm_call``.
4. ``review`` - escalate matching calls to Hermes's human-approval gate.

Everything here is pure, deterministic Python (no model calls, no I/O), so it
is cheap to run on every tool call and easy to test.
"""

from __future__ import annotations

import fnmatch
import functools
import math
import re
import shlex
import time
from typing import Any

RUNG_ORDER = ("guard", "check", "rule", "review")
RUNG_LABELS = {
    "guard": "1 impossible  (guard blocks the call)",
    "check": "2 checked     (verified after the call)",
    "rule": "3 rule        (reminded every turn)",
    "review": "4 human review (asks you first)",
}

# Tools whose arguments carry a shell command / code that could run one.
COMMAND_FIELDS: dict[str, tuple[str, ...]] = {
    "terminal": ("command",),
    "execute_code": ("code",),
}
# Tools that write files, and where their path and new content live.
FILE_WRITE_TOOLS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "write_file": (("path",), ("content",)),
    "patch": (("path",), ("new_string", "patch")),
    "skill_manage": (("file_path",), ("file_content", "new_string")),
}
PATH_KEYS = ("path", "file_path", "filepath", "target_path", "destination", "dest", "src", "source")
OWN_TOOLS = frozenset({"trust_ladder_learn", "trust_ladder_task", "trust_ladder_status"})

MAX_PATTERN_LEN = 400
MAX_SUBJECT_LEN = 20_000
MAX_RULE_TEXT = 500

# Catastrophic-backtracking shapes such as (a+)+, (\w*)*, (x|x+){2,}
_NESTED_QUANT = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\s*(?:[+*]|\{\d+,\})")


class CompileError(ValueError):
    """A guard/check spec that cannot be installed safely."""


# ----------------------------------------------------------------------------- regex helpers


def safe_regex(pattern: str, ignore_case: bool = False) -> re.Pattern:
    if not isinstance(pattern, str) or not pattern.strip():
        raise CompileError("pattern is empty")
    if len(pattern) > MAX_PATTERN_LEN:
        raise CompileError(f"pattern is longer than {MAX_PATTERN_LEN} characters")
    if _NESTED_QUANT.search(pattern):
        raise CompileError("pattern has nested quantifiers (catastrophic backtracking risk)")
    return _compiled(pattern, bool(ignore_case))


@functools.lru_cache(maxsize=512)
def _compiled(pattern: str, ignore_case: bool) -> re.Pattern:
    try:
        return re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise CompileError(f"invalid regex: {exc}") from exc


_LEADING_DOTSTAR = re.compile(r"^(?:\.\*\??)+(?=.)")
# Per-call budget for one pattern on a worst-case MAX_SUBJECT_LEN input, in seconds.
REGEX_BUDGET_S = 0.05


def simplify_pattern(pattern: str) -> str:
    """Drop a redundant leading ``.*`` (search() already scans every start position).

    ``.*git push.*main`` and ``git push.*main`` match exactly the same calls, but the
    first is quadratic on long commands.
    """
    if isinstance(pattern, str):
        stripped = _LEADING_DOTSTAR.sub("", pattern)
        if stripped and stripped != pattern:
            return stripped
    return pattern


def _probe_seeds(pattern: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_-]+", re.sub(r"\\[a-zA-Z]", " ", pattern)) or ["a"]
    chars = "".join(sorted(set("".join(words)))) or "a"
    # Inputs that contain most of the pattern's literals but never complete a match force the
    # regex engine to explore every way of matching; that is where backtracking blows up.
    seeds = [" ".join(words[:-1]) + " " if len(words) > 1 else words[0][:-1] + " ",
             " ".join(words) + " ", chars[:-1] or chars, chars, "a", " ", "a b ", "-" + words[0] + " /", "x\n"]
    return [seed for seed in dict.fromkeys(seeds) if seed]


def _profile(pattern: str, ignore_case: bool):
    """Fit worst-case search time vs. subject length on adversarial inputs.

    Returns ``estimate(n) -> seconds``. Sizes only grow while the fitted curve stays cheap at
    MAX_SUBJECT_LEN, so profiling a catastrophic pattern costs milliseconds, never minutes.
    """
    regex = safe_regex(pattern, ignore_case)
    curves: list[tuple[float, int, float]] = []  # (seconds at n, n, exponent)
    for seed in _probe_seeds(pattern):
        prev_n, prev_t, point = 0, 0.0, None
        for n in (64, 128, 256, 512, 1024):
            subject = (seed * (n // len(seed) + 1))[:n]
            best = float("inf")
            for _ in range(3):
                started = time.perf_counter()
                regex.search(subject)
                best = min(best, time.perf_counter() - started)
            best = max(best, 1e-7)
            exponent = 1.0
            if prev_n and best > 5e-5:  # below this, timer noise dominates; treat as linear
                exponent = min(4.0, max(1.0, _log2(best / prev_t) / _log2(n / prev_n)))
            point = (best, n, exponent)
            if best * (MAX_SUBJECT_LEN / n) ** exponent > REGEX_BUDGET_S:
                break  # already known to be slow at full length; no need to probe bigger inputs
            prev_n, prev_t = n, best
        curves.append(point)

    def estimate(size: int) -> float:
        return max(t * (size / n) ** e for t, n, e in curves)
    return estimate


def regex_cost(pattern: str, ignore_case: bool = False) -> float:
    """Estimated worst-case seconds for one search over a MAX_SUBJECT_LEN-character subject."""
    return _profile(pattern, ignore_case)(MAX_SUBJECT_LEN)


WINDOWS = (MAX_SUBJECT_LEN, 10_000, 5_000, 2_500, 1_200, 600)


def plan_regex(pattern: str, ignore_case: bool = False) -> int:
    """Pick the search window for a pattern so one call stays within budget.

    Python's ``re`` has no timeout: ``git.*push.*--force`` takes ~7 s on a 20,000-character
    command because each ``.*`` backtracks over the whole input. Rather than refuse such a
    natural pattern, a slow pattern is searched in overlapping windows (each match must fit
    in half a window), which bounds the cost; only a pattern too slow even on 600-character
    windows is refused (CompileError).
    """
    estimate = _profile(pattern, ignore_case)
    for window in WINDOWS:
        passes = 1 if window >= MAX_SUBJECT_LEN else 2 * MAX_SUBJECT_LEN // window
        if passes * estimate(window) <= (REGEX_BUDGET_S if passes == 1 else 2 * REGEX_BUDGET_S):
            return window
    raise CompileError(
        f"pattern would be too slow on long inputs (~{estimate(MAX_SUBJECT_LEN):.1f}s per call); "
        "avoid stacking unbounded wildcards such as .* or [a-z]* next to each other")


def vet_regex(pattern: str, ignore_case: bool = False) -> int:
    """Validate a pattern and return its search window (raises CompileError when unsafe)."""
    return plan_regex(pattern, ignore_case)


def windowed_search(regex: re.Pattern, text: str, window: int = MAX_SUBJECT_LEN):
    """``regex.search(text)``, but over overlapping windows when *text* is longer than *window*.

    Returns ``(match, offset)`` or ``(None, 0)``; ``offset`` is where the matching window starts.
    """
    if not window or len(text) <= window:
        return regex.search(text), 0
    step = max(1, window // 2)
    for start in range(0, len(text), step):
        hit = regex.search(text, start, min(len(text), start + window))
        if hit:
            return hit, 0
        if start + window >= len(text):
            break
    return None, 0


def _log2(x: float) -> float:
    return math.log(x, 2) if x > 0 else 0.0


def phrase_to_regex(phrase: str) -> str:
    """Turn a literal command phrase into a whitespace-tolerant, boundary-aware regex.

    ``git push --force`` -> ``(?<![\\w-])git\\s+push\\s+--force(?![\\w-])`` so it does
    not match ``--force-with-lease`` or ``legit push``.
    """
    tokens = phrase.strip().split()
    if not tokens:
        raise CompileError("empty command phrase")
    body = r"\s+".join(re.escape(t) for t in tokens)
    head = r"(?<![\w-])" if re.match(r"[\w-]", tokens[0]) else ""
    tail = r"(?![\w-])" if re.search(r"[\w-]$", tokens[-1]) else ""
    return head + body + tail


# ----------------------------------------------------------------------------- matching


def _clip(value: Any) -> str:
    text = value if isinstance(value, str) else ""
    return text[:MAX_SUBJECT_LEN]


def _command_subjects(tool_name: str, args: dict) -> list[str]:
    fields = COMMAND_FIELDS.get(tool_name, ())
    return [_clip(args.get(f)) for f in fields if isinstance(args.get(f), str)]


def _path_subjects(tool_name: str, args: dict) -> list[str]:
    out: list[str] = []
    for key in PATH_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val:
            out.append(val)
        elif isinstance(val, list):
            out.extend(v for v in val if isinstance(v, str))
    return out


def _glob_hit(glob: str, path: str) -> bool:
    path = path.replace("\\", "/")
    stripped = path[2:] if path.startswith("./") else path
    candidates = {path, stripped, stripped.rsplit("/", 1)[-1]}
    glob = glob.replace("\\", "/")
    base = (glob[2:] if glob.startswith("./") else glob).rstrip("/") or glob
    patterns = {glob, base}
    if base.startswith("**/"):
        patterns.add(base[3:])
    if "/**/" in base:  # gitignore-style: ** may match zero directories
        patterns.add(base.replace("/**/", "/"))
    if not base.startswith("/"):
        # a relative glob also matches the same relative path below any root
        patterns.add("*/" + base)
    if not any(ch in base for ch in "*?["):
        # a bare directory or file: match it and anything below it
        patterns.update({base + "/*", "*/" + base + "/*"} if not base.startswith("/") else {base + "/*"})
    elif base.endswith("/**"):
        patterns.add(base[:-3])
    patterns |= {pat.replace("/**/", "/") for pat in patterns if "/**/" in pat}
    for cand in candidates:
        for pat in patterns:
            if fnmatch.fnmatchcase(cand, pat):
                return True
    return False


def _command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


_WRITEISH_SHELL = re.compile(
    r"(?<![\w-])(rm|mv|cp|tee|truncate|sed\s+-i|chmod|chown|ln|touch|dd|>|>>)(?![\w-])"
)


def evaluate_match(match: dict, tool_name: str, args: Any) -> str | None:
    """Return the evidence string when ``match`` hits this tool call, else ``None``."""
    if not isinstance(match, dict) or not match or not isinstance(args, dict):
        return None
    if tool_name in OWN_TOOLS:
        return None
    tools = match.get("tools") or ["*"]
    kind = match.get("kind", "regex")
    field = match.get("field", "any")
    ignore_case = bool(match.get("ignore_case", False))

    if kind == "tool":
        return f"tool {tool_name}" if tool_name in tools else None

    if "*" not in tools and tool_name not in tools:
        return None

    unless = match.get("unless")
    unless_re = safe_regex(unless, ignore_case) if unless else None
    unless_window = int(match.get("unless_window") or MAX_SUBJECT_LEN)

    if kind == "path_glob":
        glob = match.get("pattern", "")
        if tool_name in COMMAND_FIELDS:
            if not match.get("include_shell", True):
                return None
            for command in _command_subjects(tool_name, args):
                if not _WRITEISH_SHELL.search(command):
                    continue
                for token in _command_tokens(command):
                    if token.startswith("-"):
                        continue
                    if _glob_hit(glob, token):
                        return f"{field or 'command'} touches {token}"
            return None
        for path in _path_subjects(tool_name, args):
            if _glob_hit(glob, path):
                if unless_re and windowed_search(unless_re, path, unless_window)[0]:
                    continue
                return f"path {path}"
        return None

    # regex over a field
    regex = safe_regex(match.get("pattern", ""), ignore_case)
    if field == "command":
        subjects = _command_subjects(tool_name, args)
    elif field == "path":
        subjects = _path_subjects(tool_name, args)
    elif field in ("any", "*", "", None):
        subjects = [_clip(v) for v in args.values() if isinstance(v, str)]
    else:
        subjects = [_clip(args.get(field))] if isinstance(args.get(field), str) else []
    window = int(match.get("window") or MAX_SUBJECT_LEN)
    for subject in subjects:
        hit, _ = windowed_search(regex, subject, window)
        if hit and not (unless_re and windowed_search(unless_re, subject, unless_window)[0]):
            return hit.group(0)[:200]
    return None


def evaluate_check(check: dict, tool_name: str, args: Any, result: Any) -> str | None:
    """Return a failure description when a post-call check fails, else ``None``."""
    if not isinstance(check, dict) or not isinstance(args, dict) or tool_name in OWN_TOOLS:
        return None
    tools = check.get("tools")
    if tools and "*" not in tools and tool_name not in tools:
        return None
    ignore_case = bool(check.get("ignore_case", False))

    result_bad = check.get("result_must_not_match")
    if result_bad and isinstance(result, str):
        hit, _ = windowed_search(safe_regex(result_bad, ignore_case), result[:MAX_SUBJECT_LEN],
                                 int((check.get("windows") or {}).get("result_must_not_match") or MAX_SUBJECT_LEN))
        if hit:
            return f"{tool_name} output contains `{hit.group(0)[:120]}`"

    spec = FILE_WRITE_TOOLS.get(tool_name)
    if not spec:
        return None
    path_keys, content_keys = spec
    path = next((args.get(k) for k in path_keys if isinstance(args.get(k), str)), "")
    glob = check.get("path_glob") or "*"
    if not path or not _glob_hit(glob, path):
        return None
    bad = check.get("must_not_match")
    if bad:
        regex = safe_regex(bad, ignore_case)
        for key in content_keys:
            content = args.get(key)
            if isinstance(content, str):
                hit, _ = windowed_search(regex, content[:MAX_SUBJECT_LEN * 10],
                                         int((check.get("windows") or {}).get("must_not_match") or MAX_SUBJECT_LEN))
                if hit:
                    line = content.count("\n", 0, hit.start()) + 1
                    return f"{path} contains `{hit.group(0)[:120]}` (line {line} of the written text)"
    good = check.get("must_match")
    if good and tool_name == "write_file":  # only a full write shows the whole file
        content = args.get("content")
        if isinstance(content, str) and not windowed_search(
                safe_regex(good, ignore_case), content[:MAX_SUBJECT_LEN * 10],
                int((check.get("windows") or {}).get("must_match") or MAX_SUBJECT_LEN))[0]:
            return f"{path} is missing required text matching `{good}`"
    return None


# ----------------------------------------------------------------------------- spec validation


def normalize_match(spec: dict) -> dict:
    """Validate a guard/review matcher spec from the model or the parser."""
    if not isinstance(spec, dict):
        raise CompileError("guard must be an object")
    tool = spec.get("tool") or spec.get("tools") or "*"
    tools = [tool] if isinstance(tool, str) else list(tool)
    tools = [str(t).strip() for t in tools if str(t).strip()] or ["*"]
    kind = str(spec.get("kind") or "regex").strip()
    if kind not in ("regex", "path_glob", "tool"):
        raise CompileError("kind must be regex, path_glob or tool")
    field = str(spec.get("field") or ("path" if kind == "path_glob" else "any")).strip()
    pattern = spec.get("pattern") or ""
    window = MAX_SUBJECT_LEN
    if kind == "tool":
        if "*" in tools:
            raise CompileError("a tool-level guard must name the tool, not '*'")
        if any(t in OWN_TOOLS for t in tools):
            raise CompileError("trust-ladder's own tools cannot be guarded")
        return {"tools": tools, "kind": "tool", "field": "", "pattern": ""}
    if kind == "path_glob":
        if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > MAX_PATTERN_LEN:
            raise CompileError("path_glob pattern must be a non-empty glob")
        if pattern.strip() in ("*", "**", "**/*", "/", "."):
            raise CompileError("path_glob is too broad; it would block every file")
    else:
        if re.fullmatch(r"\.?[*+]?|\^?\.\*\$?", str(pattern).strip()):
            raise CompileError("regex is too broad; it would match every call")
        pattern = simplify_pattern(pattern)
        window = vet_regex(pattern, bool(spec.get("ignore_case")))
    out = {"tools": tools, "kind": kind, "field": field, "pattern": pattern}
    if kind == "regex" and window < MAX_SUBJECT_LEN:
        out["window"] = window
    if spec.get("unless"):
        unless = simplify_pattern(str(spec["unless"]))
        unless_window = vet_regex(unless, bool(spec.get("ignore_case")))
        out["unless"] = unless
        if unless_window < MAX_SUBJECT_LEN:
            out["unless_window"] = unless_window
    if spec.get("ignore_case"):
        out["ignore_case"] = True
    if kind == "path_glob" and spec.get("include_shell") is False:
        out["include_shell"] = False
    return out


def normalize_check(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise CompileError("check must be an object")
    out: dict[str, Any] = {}
    for key in ("must_not_match", "must_match", "result_must_not_match"):
        if spec.get(key):
            pattern = simplify_pattern(str(spec[key]))
            window = vet_regex(pattern, bool(spec.get("ignore_case")))
            out[key] = pattern
            if window < MAX_SUBJECT_LEN:
                out.setdefault("windows", {})[key] = window
    if not out:
        raise CompileError("check needs must_not_match, must_match or result_must_not_match")
    if spec.get("path_glob"):
        out["path_glob"] = str(spec["path_glob"])
    tools = spec.get("tool") or spec.get("tools")
    if tools:
        out["tools"] = [tools] if isinstance(tools, str) else list(tools)
    elif "result_must_not_match" not in out:
        out["tools"] = list(FILE_WRITE_TOOLS)
    if spec.get("ignore_case"):
        out["ignore_case"] = True
    return out


def _example(example: Any) -> tuple[str, dict] | None:
    if not isinstance(example, dict):
        return None
    tool = example.get("tool")
    args = example.get("args")
    if isinstance(tool, str) and isinstance(args, dict):
        return tool, args
    return None


def verify_against_examples(rung: str, compiled: dict, bad: Any, good: Any) -> list[str]:
    """Prove the compiled guard/check catches the mistake and spares the fix.

    Returns a list of problems (empty = verified).
    """
    problems: list[str] = []
    bad_ex, good_ex = _example(bad), _example(good)
    if rung in ("guard", "review"):
        if bad_ex is None:
            problems.append("no bad_example to prove the guard catches the mistake")
        elif evaluate_match(compiled, *bad_ex) is None:
            problems.append("the guard does not match the bad_example (it would not have caught the mistake)")
        if good_ex is not None and evaluate_match(compiled, *good_ex) is not None:
            problems.append("the guard also matches the good_example (it would block the correct call)")
    elif rung == "check":
        if bad_ex is not None and evaluate_check(compiled, bad_ex[0], bad_ex[1], bad_ex[1].get("result", "")) is None:
            problems.append("the check does not fail on the bad_example")
        if good_ex is not None and evaluate_check(compiled, good_ex[0], good_ex[1], good_ex[1].get("result", "")) is not None:
            problems.append("the check also fails on the good_example")
    return problems


# ----------------------------------------------------------------------------- natural language


_QUOTED = re.compile(r"`([^`]+)`|\"([^\"]+)\"|“([^”]+)”|'([^']{2,})'")
_SLASH_REGEX = re.compile(r"(?<!\S)/(.+?)/(i?)(?!\S)")

_NEG = r"(?:never|don'?t|do not|dont|stop|no more|avoid|quit|must not|mustn'?t|shouldn'?t|should not|you may not|not allowed to)"
_RUN_VERBS = r"(?:run|running|use|using|execute|executing|call|calling|type|typing|invoke|invoking|do|doing)"
_EDIT_VERBS = (r"(?:edit|editing|modify|modifying|write to|writing to|write|writing|touch|touching|change|changing|"
               r"delete|deleting|remove|removing|overwrite|overwriting|commit to|commit)")

# Idioms that have no quoted command but a precise meaning.
_IDIOMS: list[tuple[re.Pattern, dict, str]] = [
    (re.compile(r"\bforce[- ]?push(es|ing)?\b", re.I),
     {"tools": ["terminal", "execute_code"], "kind": "regex", "field": "command",
      "pattern": r"(?<![\w-])git\s+push\b(?=[^\n;&|]*\s(?:--force(?![\w-])|-f(?![\w-])|\+\S))"},
     "git push --force origin main"),
    (re.compile(r"\b(rm -rf|rm -fr)\b", re.I),
     {"tools": ["terminal", "execute_code"], "kind": "regex", "field": "command",
      "pattern": r"(?<![\w-])rm\s+-(?:[a-zA-Z]*r[a-zA-Z]*f|[a-zA-Z]*f[a-zA-Z]*r)[a-zA-Z]*(?![\w-])"},
     "rm -rf build"),
    (re.compile(r"\b(skip|bypass|disable)\w*\s+(the\s+)?(pre-?commit\s+)?hooks?\b|--no-verify", re.I),
     {"tools": ["terminal", "execute_code"], "kind": "regex", "field": "command",
      "pattern": r"(?<![\w-])git\s+(?:commit|push)\b[^\n]*\s--no-verify(?![\w-])"},
     "git commit -m wip --no-verify"),
    (re.compile(r"\bpush(es|ing)?\s+(?:(?:directly|straight|right)\s+)?(?:in)?to\s+(?:the\s+)?(?:origin[/ ])?(main|master)\b", re.I),
     {"tools": ["terminal", "execute_code"], "kind": "regex", "field": "command",
      "pattern": r"(?<![\w-])git\s+push\b[^\n;&|]*\s(?:origin\s+)?(?:HEAD:)?(?:main|master)(?![\w-])"},
     "git push origin main"),
]

_KNOWN_CLIS = {
    "git", "npm", "npx", "yarn", "pnpm", "pip", "pip3", "uv", "poetry", "python", "python3", "node", "docker",
    "kubectl", "terraform", "rm", "mv", "cp", "sudo", "curl", "wget", "make", "cargo", "go", "brew", "apt",
    "apt-get", "chmod", "chown", "ssh", "scp", "rsync", "sed", "awk", "psql", "mysql", "redis-cli", "aws",
    "gcloud", "az", "heroku", "vercel", "gh", "black", "ruff", "pytest", "jest", "eslint", "prettier", "tsc",
    "deno", "bun", "helm", "systemctl", "service", "kill", "pkill", "killall", "dd", "shutdown",
    "reboot", "crontab", "find", "xargs", "tar", "zip", "unzip", "conda", "mamba", "pipx", "twine",
}


def _looks_like_path(target: str) -> bool:
    t = target.strip()
    if not t or " " in t:
        return False
    return (
        "/" in t or t.startswith(".") or t.startswith("~") or any(c in t for c in "*?")
        or bool(re.search(r"\.[A-Za-z0-9]{1,6}$", t))
    )


def _looks_like_command(target: str) -> bool:
    tokens = target.strip().split()
    if not tokens:
        return False
    first = tokens[0].lower()
    return first in _KNOWN_CLIS or any(tok.startswith("-") for tok in tokens[1:])


def _quoted(text: str) -> list[str]:
    return [next(g for g in m.groups() if g) for m in _QUOTED.finditer(text)]


def _strip_tail(phrase: str) -> str:
    phrase = re.split(r"\s+(?:instead|again|anymore|any more|ever|please|without asking)\b|[;,.!]\s|[;,.!]$", phrase)[0]
    return phrase.strip().strip("`'\"").strip()


def _fix_hint(text: str) -> str:
    m = re.search(r"\b(?:use|run|prefer|do)\s+(.+?)\s+instead\b", text, re.I) or \
        re.search(r"\binstead[,]?\s+(?:use|run|do)\s+(.+?)(?:[.;!]|$)", text, re.I) or \
        re.search(r"[;,.]\s*(?:use|run)\s+(.+?)(?:[.;!]|$)", text, re.I)
    return m.group(1).strip().strip("`'\"") if m else ""


def _command_match(phrase: str) -> dict:
    return {"tools": ["terminal", "execute_code"], "kind": "regex", "field": "command",
            "pattern": phrase_to_regex(phrase)}


def _path_match(glob: str) -> dict:
    return {"tools": list(FILE_WRITE_TOOLS) + ["terminal"], "kind": "path_glob", "field": "path",
            "pattern": glob.strip().strip("`'\"")}


def compile_text(text: str) -> dict:
    """Compile a natural-language correction to the best rung we can prove.

    Returns ``{"rung", "match"?, "check"?, "fix"?, "selftest"?, "why"}``.
    """
    raw = " ".join((text or "").split())
    lowered = raw.lower()
    quoted = _quoted(raw)
    fix = _fix_hint(raw)

    # --- explicit /regex/ -----------------------------------------------------
    slash = _SLASH_REGEX.search(raw)
    is_review = bool(re.match(r"^(?:always\s+)?(?:ask|check with|confirm with)\s+me\b.*\bbefore\b", lowered))

    # --- check rung: "<glob> must not contain X" / "never leave X in <glob>" ---
    m = re.search(r"(?:files?\s+(?:in|under|matching)\s+)?(\S+)\s+(?:must|should)\s+(never|not|always)?\s*contain\s+(.+)$",
                  raw, re.I)
    if m and _looks_like_path(m.group(1)):
        glob, mode, needle = m.group(1).strip("`'\""), (m.group(2) or "").lower(), m.group(3)
        needle_q = _quoted(needle)
        literal = needle_q[0] if needle_q else _strip_tail(needle)
        key = "must_match" if mode == "always" or mode == "" else "must_not_match"
        check = {"path_glob": glob, key: re.escape(literal), "tools": list(FILE_WRITE_TOOLS)}
        return {"rung": "check", "check": check, "fix": fix,
                "why": f"compiled to a post-write check on {glob}"}
    m = re.search(rf"{_NEG}\s+(?:leave|commit|write|put|add|ship)\s+(.+?)\s+(?:in|into|to)\s+(\S+)", raw, re.I)
    if m and _looks_like_path(m.group(2)):
        needle_q = _quoted(m.group(1))
        literal = needle_q[0] if needle_q else m.group(1).strip("`'\"")
        glob = m.group(2).strip("`'\"").rstrip(".,;:!")
        check = {"path_glob": glob, "must_not_match": re.escape(literal), "tools": list(FILE_WRITE_TOOLS)}
        return {"rung": "check", "check": check, "fix": fix,
                "why": f"compiled to a post-write check on {glob}"}

    rung = "review" if is_review else "guard"
    negative = is_review or bool(re.search(rf"\b{_NEG}\b", lowered))

    # "use X instead of Y" / "prefer X over Y" / "use X, not Y"
    m = re.search(r"\b(?:use|prefer|run)\s+(.+?)\s+(?:instead of|rather than|over|not)\s+(.+?)(?:[.;!]|$)", raw, re.I) or \
        re.search(r"\binstead of\s+(.+?)[,]?\s+(?:use|run)\s+(.+?)(?:[.;!]|$)", raw, re.I)
    if m:
        if raw.lower().lstrip().startswith("instead of"):
            bad_phrase, good_phrase = m.group(1), m.group(2)
        else:
            good_phrase, bad_phrase = m.group(1), m.group(2)
        bad_phrase = _strip_tail(bad_phrase)
        good_phrase = _strip_tail(good_phrase)
        if _looks_like_command(bad_phrase) or bad_phrase.split()[0].lower() in _KNOWN_CLIS:
            match = _command_match(bad_phrase)
            return {"rung": "guard", "match": match, "fix": f"use {good_phrase}",
                    "selftest": {"bad": ("terminal", {"command": bad_phrase}),
                                 "good": ("terminal", {"command": good_phrase})},
                    "why": f"compiled `{bad_phrase}` into a command guard"}

    if negative or slash:
        if slash:
            pattern = slash.group(1)
            match = {"tools": ["terminal", "execute_code"], "kind": "regex", "field": "command", "pattern": pattern}
            if slash.group(2):
                match["ignore_case"] = True
            if re.search(rf"\b{_EDIT_VERBS}\b", lowered) and not re.search(rf"\b{_RUN_VERBS}\b", lowered):
                match = {"tools": ["*"], "kind": "regex", "field": "path", "pattern": pattern}
            return {"rung": rung, "match": match, "fix": fix, "why": "compiled your /regex/ into a guard"}

        for pattern, match, sample in _IDIOMS:
            if pattern.search(raw) and not quoted:
                return {"rung": rung, "match": dict(match), "fix": fix,
                        "selftest": {"bad": ("terminal", {"command": sample})},
                        "why": "recognised a known risky command and compiled a guard"}

        edit_verb = re.search(rf"\b{_EDIT_VERBS}\b\s+(?:the\s+|any\s+|files?\s+(?:in|under)\s+)?(\S+)", raw, re.I)
        run_verb = re.search(rf"\b{_RUN_VERBS}\b\s+(.+)", raw, re.I)

        # A quoted target is the strongest signal.
        for target in quoted:
            if _looks_like_path(target) and edit_verb:
                return {"rung": rung, "match": _path_match(target), "fix": fix,
                        "selftest": {"bad": ("write_file", {"path": target.replace("**/", "").replace("*", "x"),
                                                             "content": ""})},
                        "why": f"compiled `{target}` into a path guard"}
            if target.strip():
                tool_target = re.fullmatch(r"(?:tool:)?([a-z][a-z0-9_]{2,63})", target.strip())
                if tool_target and "tool" in lowered and " " not in target:
                    return {"rung": rung, "match": {"tools": [tool_target.group(1)], "kind": "tool",
                                                    "field": "", "pattern": ""},
                            "fix": fix, "why": f"compiled a tool-level guard on {tool_target.group(1)}"}
                return {"rung": rung, "match": _command_match(target), "fix": fix,
                        "selftest": {"bad": ("terminal", {"command": target})},
                        "why": f"compiled `{target}` into a command guard"}

        if edit_verb and _looks_like_path(edit_verb.group(1).strip("`'\"").rstrip(".,;:!")):
            target = edit_verb.group(1).strip("`'\"").rstrip(".,;:!")
            return {"rung": rung, "match": _path_match(target), "fix": fix,
                    "selftest": {"bad": ("write_file", {"path": target.replace("**/", "").replace("*", "x"),
                                                         "content": ""})},
                    "why": f"compiled `{target}` into a path guard"}

        m = re.search(r"\b(?:the\s+)?([a-z][a-z0-9_]{2,63})\s+tool\b", raw, re.I)
        if m and re.search(rf"\b{_RUN_VERBS}\b", lowered):
            return {"rung": rung, "match": {"tools": [m.group(1)], "kind": "tool", "field": "", "pattern": ""},
                    "fix": fix, "why": f"compiled a tool-level guard on {m.group(1)}"}

        if run_verb:
            phrase = _strip_tail(run_verb.group(1))
            if phrase and _looks_like_command(phrase):
                return {"rung": rung, "match": _command_match(phrase), "fix": fix,
                        "selftest": {"bad": ("terminal", {"command": phrase})},
                        "why": f"compiled `{phrase}` into a command guard"}

    return {"rung": "rule", "fix": fix,
            "why": "no deterministic matcher found; kept as a rule (the model can promote it with trust_ladder_learn)"}


def selftest(result: dict) -> list[str]:
    """Run the parser's own example through the compiled matcher."""
    test = result.get("selftest") or {}
    problems = []
    if result.get("rung") in ("guard", "review") and test.get("bad"):
        if evaluate_match(result["match"], *test["bad"]) is None:
            problems.append("guard did not match its own example")
        if test.get("good") and evaluate_match(result["match"], *test["good"]) is not None:
            problems.append("guard also matched the recommended alternative")
    return problems
