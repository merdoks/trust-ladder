"""Runtime: hooks, tools and commands for trust-ladder.

All state goes through ``store`` (plugin data dir). All matching goes through
``compiler`` (pure functions). This module wires the two to Hermes.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import time
from typing import Any, Callable, Optional

from . import compiler, store

logger = logging.getLogger(__name__)

MAX_RULES = 200
MAX_TASKS = 50
MAX_VERIFY_TIMEOUT = 600  # Hermes's default foreground cap; longer runs would go to the background
SLOW_RULE_S = 0.25
_SECRETISH = re.compile(
    r"(?i)(\b(?:password|passwd|pwd|secret|token|api[_-]?key|auth(?:orization)?|bearer|access[_-]?key)"
    r"\b['\"]?\s*[=:]?\s*(?:bearer\s+)?['\"]?)([^\s'\"&]{6,})"
    r"|()(\b(?:sk|pk|rk|ghp|gho|ghs|github_pat|xox[abpr]|glpat|AKIA|ASIA|AIza)[-_A-Za-z0-9]{8,})"
    r"|()(\b(?=[A-Za-z0-9+_-]*\d)(?=[A-Za-z0-9+_-]*[A-Za-z])[A-Za-z0-9+_-]{32,}={0,2})")


def _mask(m: "re.Match[str]") -> str:
    prefix = m.group(1) or m.group(3) or m.group(5) or ""
    secret = m.group(2) or m.group(4) or m.group(6) or ""
    return f"{prefix}{secret[:3]}…[redacted]"


def redact(text: Any) -> str:
    """Mask secret-looking tokens before anything is written to the ledger or shown in a notice."""
    return _SECRETISH.sub(_mask, str(text or ""))[:200]


DONE_CLAIM = re.compile(
    r"\b(done|complete[d]?|finished|all set|fixed|resolved|ready to (?:merge|ship)|passing|works now|shipped)\b", re.I)
CORRECTION_HINT = re.compile(
    r"(?:^|\b)(?:no[,!.]|nope|wrong|don'?t|do not|never|stop|i told you|i said|instead|not like that|"
    r"you (?:shouldn'?t|should not|keep)|why did you)\b", re.I)


def _fmt_time(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return "-"


def _describe_match(match: dict) -> str:
    if not match:
        return ""
    tools = ",".join(match.get("tools") or ["*"])
    kind = match.get("kind")
    if kind == "tool":
        return f"tool {tools}"
    if kind == "path_glob":
        return f"{tools} path ~ {match.get('pattern')}"
    extra = f" unless /{match['unless']}/" if match.get("unless") else ""
    return f"{tools} {match.get('field', 'any')} =~ /{match.get('pattern')}/{extra}"


def _describe_check(check: dict) -> str:
    parts = []
    if check.get("path_glob"):
        parts.append(f"files {check['path_glob']}")
    if check.get("must_not_match"):
        parts.append(f"must not contain /{check['must_not_match']}/")
    if check.get("must_match"):
        parts.append(f"must contain /{check['must_match']}/")
    if check.get("result_must_not_match"):
        parts.append(f"output must not contain /{check['result_must_not_match']}/")
    return ", ".join(parts)


class TrustLadder:
    """One instance per loaded plugin; holds the ctx for dispatch/config/cron lookups."""

    def __init__(self, ctx: Any = None):
        self.ctx = ctx

    # ------------------------------------------------------------------ config

    def setting(self, key: str, default: Any) -> Any:
        if self.ctx is None:
            return default
        try:
            value = self.ctx.get_config(key, default=default)
        except Exception:
            return default
        return default if value is None else value

    def enforcing(self) -> bool:
        return str(self.setting("mode", "enforce")).lower() != "observe"

    def unattended(self) -> bool:
        if self.ctx is None:
            return False
        try:
            return self.ctx.current_cron_execution() is not None
        except Exception:
            return False

    # ------------------------------------------------------------------ rules cache

    def rules(self) -> list[dict]:
        """Read rules.json on every call; deliberately no cache.

        An mtime-keyed cache served stale rules when another process (the CLI, a second session)
        changed them inside one coarse clock tick: Linux stamps files with a jiffy-grained clock,
        so back-to-back writes usually keep the same mtime_ns. TLC found the path (docs/statechart,
        RuleLadder.tla); reading a few KB of JSON per tool call is cheap and makes it impossible.
        """
        return store.load_rules()

    def invalidate(self) -> None:
        """Kept for callers; there is no cache to drop."""

    # ------------------------------------------------------------------ learning

    def learn(self, text: str, *, source: str, guard: dict | None = None, check: dict | None = None,
              review: bool = False, fix: str = "", bad_example: Any = None, good_example: Any = None,
              session_id: str = "") -> dict:
        """Compile and store a correction. Returns {ok, rule, rung, why, problems}."""
        text = " ".join(str(text or "").split())[: compiler.MAX_RULE_TEXT]
        if not text:
            return {"ok": False, "error": "empty rule"}
        if len([r for r in self.rules() if r.get("enabled", True)]) >= MAX_RULES:
            return {"ok": False, "error": f"rule limit reached ({MAX_RULES}); ask the user to prune with /trust remove"}

        problems: list[str] = []
        record: dict[str, Any] = {"text": text, "source": source, "fix": str(fix or "")[:200]}
        rung = None

        if guard:
            try:
                match = compiler.normalize_match(guard)
                want = "review" if review else "guard"
                issues = compiler.verify_against_examples(want, match, bad_example, good_example)
                if issues:
                    problems.extend(f"guard rejected: {p}" for p in issues)
                else:
                    rung, record["match"] = want, match
                    record["verified"] = "examples"
            except compiler.CompileError as exc:
                problems.append(f"guard rejected: {exc}")

        if rung is None and check:
            try:
                normalized = compiler.normalize_check(check)
                issues = compiler.verify_against_examples("check", normalized, bad_example, good_example)
                if issues:
                    problems.extend(f"check rejected: {p}" for p in issues)
                else:
                    rung, record["check"] = "check", normalized
                    record["verified"] = "examples" if bad_example else "unverified"
            except compiler.CompileError as exc:
                problems.append(f"check rejected: {exc}")

        why = ""
        if rung is None:
            parsed = compiler.compile_text(text)
            issues = compiler.selftest(parsed)
            if parsed["rung"] in ("guard", "review") and review:
                parsed["rung"] = "review"
            if issues:
                problems.extend(f"parser: {p}" for p in issues)
                parsed = {"rung": "rule", "why": "parsed matcher failed its self-test; kept as a rule"}
            # Same safety gates as a model-supplied spec (catch-alls, slow regexes, windows).
            try:
                if parsed.get("match"):
                    parsed["match"] = compiler.normalize_match(parsed["match"])
                if parsed.get("check"):
                    parsed["check"] = compiler.normalize_check(parsed["check"])
            except compiler.CompileError as exc:
                problems.append(f"parser: {exc}")
                parsed = {"rung": "rule", "why": f"parsed matcher was unsafe ({exc}); kept as a rule"}
            # A model-supplied bad example must also be caught by a parsed guard.
            if parsed["rung"] in ("guard", "review") and bad_example is not None:
                extra = compiler.verify_against_examples(parsed["rung"], parsed["match"], bad_example, good_example)
                if extra:
                    problems.extend(f"parser: {p}" for p in extra)
                    parsed = {"rung": "rule", "why": "parsed guard did not fit the examples; kept as a rule"}
            rung = parsed["rung"]
            why = parsed.get("why", "")
            if rung == "review":
                why = why.replace("into a command guard", "into an approval rule").replace(
                    "into a path guard", "into an approval rule")
            if parsed.get("match"):
                record["match"] = parsed["match"]
                record["verified"] = "self-test"
            if parsed.get("check"):
                record["check"] = parsed["check"]
                record["verified"] = "self-test"
            if not record["fix"] and parsed.get("fix"):
                record["fix"] = parsed["fix"]
        else:
            why = f"compiled the supplied {rung} spec and verified it against your examples"

        record["rung"] = rung
        rule = store.add_rule(record)
        self.invalidate()
        store.append_ledger({"type": "learned", "rule": rule["id"], "rung": rung, "source": source,
                             "session": session_id})
        return {"ok": True, "rule": rule, "rung": rung, "why": why, "problems": problems}

    # ------------------------------------------------------------------ hooks

    def on_pre_tool_call(self, tool_name: str = "", args: Any = None, task_id: str = "", **kwargs: Any):
        try:
            return self._pre_tool_call(tool_name, args, task_id, **kwargs)
        except Exception:  # never let a plugin bug wedge the agent; logged for the user
            logger.exception("trust-ladder: pre_tool_call failed; letting the call through")
            return None

    def _pre_tool_call(self, tool_name: str, args: Any, task_id: str, **kwargs: Any):
        if not isinstance(args, dict) or tool_name in compiler.OWN_TOOLS:
            return None
        session = kwargs.get("session_id") or task_id or ""
        review_hit: Optional[tuple[dict, str]] = None
        for rule in self.rules():
            if not rule.get("enabled", True) or rule.get("rung") not in ("guard", "review"):
                continue
            started = time.perf_counter()
            try:
                evidence = compiler.evaluate_match(rule.get("match") or {}, tool_name, args)
            except Exception:  # one broken rule must not switch off the rules after it
                logger.warning("trust-ladder: rule %s could not be evaluated; skipped", rule.get("id"),
                               exc_info=True)
                continue
            elapsed = time.perf_counter() - started
            if elapsed > SLOW_RULE_S:
                logger.warning("trust-ladder: rule %s took %.2fs on a %s call", rule.get("id"), elapsed, tool_name)
            if evidence is None:
                continue
            evidence = redact(evidence)
            if rule["rung"] == "guard":
                if not self.enforcing():
                    store.append_ledger({"type": "observe", "rule": rule["id"], "tool": tool_name,
                                         "evidence": evidence, "session": session})
                    continue
                store.bump_catch(rule["id"])
                store.append_ledger({"type": "block", "rule": rule["id"], "tool": tool_name,
                                     "evidence": evidence, "session": session})
                self.invalidate()
                self.notify(f"🪜 trust-ladder blocked {tool_name}: {evidence[:80]} (rule {rule['id']}: "
                            f"\"{rule['text'][:80]}\")")
                return {"action": "block", "message": self.block_message(rule, tool_name, evidence)}
            if review_hit is None:
                review_hit = (rule, evidence)
        if review_hit is not None:
            rule, evidence = review_hit
            store.bump_catch(rule["id"])
            self.invalidate()
            # Always hand the decision to Hermes's own approval gate, never decide it here. In a cron
            # job, a -q run or a webhook the gate resolves instantly from approvals.cron_mode /
            # single_query_mode / unattended_mode (default deny), so nothing waits on a person.
            store.append_ledger({"type": "review_unattended" if self.unattended() else "review",
                                 "rule": rule["id"], "tool": tool_name, "evidence": evidence, "session": session})
            return {"action": "approve", "rule_key": f"trust-ladder:{rule['id']}",
                    "message": f"trust-ladder rule {rule['id']}: \"{rule['text']}\" (matched {evidence})"}
        return None

    def notify(self, text: str) -> None:
        """Show the user a one-line catch notice in their conversation (best effort, never raises)."""
        if self.ctx is None or not self.setting("notify_catches", True):
            return
        try:
            post = getattr(self.ctx, "post_message", None)
            if callable(post):
                post(text)
        except Exception:
            logger.debug("trust-ladder: could not post catch notice", exc_info=True)

    @staticmethod
    def block_message(rule: dict, tool_name: str, evidence: str) -> str:
        lines = [
            f"BLOCKED by trust-ladder rule {rule['id']} (rung 1 guard). This {tool_name} call did not run.",
            f"The user's standing correction: \"{rule['text']}\"",
            f"Matched: {evidence}",
        ]
        if rule.get("fix"):
            lines.append(f"Do instead: {rule['fix']}")
        lines.append("Do not retry this call or work around the guard. If the user wants it lifted, "
                     f"they can run /trust disable {rule['id']}.")
        return "\n".join(lines)

    def on_transform_tool_result(self, tool_name: str = "", args: Any = None, result: Any = None, **kwargs: Any):
        try:
            return self._transform(tool_name, args, result, **kwargs)
        except Exception:
            logger.exception("trust-ladder: check failed to run")
            return None

    def _transform(self, tool_name: str, args: Any, result: Any, **kwargs: Any):
        if not isinstance(result, str) or not isinstance(args, dict) or tool_name in compiler.OWN_TOOLS:
            return None
        if kwargs.get("status") == "error":
            return None
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and parsed.get("error") and len(parsed) <= 3:
                return None  # the call failed; nothing was written
        except (ValueError, TypeError):
            pass
        session = kwargs.get("session_id") or kwargs.get("task_id") or ""
        failures = []
        for rule in self.rules():
            if not rule.get("enabled", True) or rule.get("rung") != "check":
                continue
            try:
                problem = compiler.evaluate_check(rule.get("check") or {}, tool_name, args, result)
            except Exception:
                logger.warning("trust-ladder: check %s could not be evaluated; skipped", rule.get("id"),
                               exc_info=True)
                continue
            if problem:
                problem = redact(problem)
                failures.append((rule, problem))
                store.bump_catch(rule["id"])
                store.append_ledger({"type": "check_failed", "rule": rule["id"], "tool": tool_name,
                                     "evidence": problem, "session": session})
        if not failures:
            return None
        self.invalidate()
        block = ["", "---", f"trust-ladder: {len(failures)} check{'s' if len(failures) > 1 else ''} FAILED after this call"]
        for rule, problem in failures:
            line = f"- rule {rule['id']}: {problem}. Correction: \"{rule['text']}\""
            if rule.get("fix"):
                line += f" Do instead: {rule['fix']}."
            block.append(line)
        block.append("Fix this now, before continuing with anything else.")
        return result + "\n" + "\n".join(block)

    def on_pre_llm_call(self, session_id: str = "", user_message: str = "", **kwargs: Any):
        try:
            return self._pre_llm(session_id, user_message, **kwargs)
        except Exception:
            logger.exception("trust-ladder: pre_llm_call failed")
            return None

    def _pre_llm(self, session_id: str, user_message: str, **kwargs: Any):
        parts: list[str] = []
        rules = [r for r in self.rules() if r.get("enabled", True)]
        if rules and self.setting("inject_rules", True):
            limit = _int(self.setting("max_injected_rules", 20), 20, lo=1, hi=200)
            plain = [r for r in rules if r.get("rung") == "rule"][:limit]
            enforced = [r for r in rules if r.get("rung") in ("guard", "check", "review")]
            if plain or enforced:
                parts.append("[trust-ladder] Standing corrections from this user:")
                for r in plain:
                    parts.append(f"- {r['text']}")
                if enforced:
                    shown = enforced[:limit]
                    parts.append(f"Enforced automatically ({len(enforced)}; matching calls are blocked, checked "
                                 "or sent for approval):")
                    parts.extend(f"- [{r['id']}] {r['text']}" for r in shown)
                    if len(enforced) > len(shown):
                        parts.append(f"- ...and {len(enforced) - len(shown)} more (/trust lists them)")
                promotable = [r for r in plain if r.get("source") == "teach"]
                if promotable:
                    parts.append("If one of the plain rules above can be expressed as a tool-call matcher, "
                                 "promote it with trust_ladder_learn (same rule text, plus guard and bad_example).")
        open_tasks = [t for t in store.load_tasks() if t.get("status") != "passed"
                      and (not t.get("session_id") or t.get("session_id") == session_id)]
        if open_tasks:
            parts.append("[trust-ladder] Verified tasks not yet passed (do not report them done until "
                         "trust_ladder_task action=verify passes):")
            parts.extend(f"- {t['id']} {t['name']}: verifier `{t['verifier']}` ({t.get('status', 'open')})"
                         for t in open_tasks[:10])
        if self.setting("correction_nudge", True) and user_message and CORRECTION_HINT.search(user_message[:2000]):
            parts.append("[trust-ladder] If this message corrects something you did, call trust_ladder_learn "
                         "with the correction (and a guard + bad_example when the mistake was a tool call) "
                         "so it is enforced from now on.")
        return {"context": "\n".join(parts)} if parts else None

    def on_pre_verify(self, session_id: str = "", attempt: int = 0, **kwargs: Any):
        try:
            if attempt and attempt >= 2:
                return None
            pending = [t for t in store.load_tasks() if t.get("status") != "passed"
                       and t.get("session_id") == session_id]
            if not pending:
                return None
            names = ", ".join(f"{t['id']} '{t['name']}'" for t in pending[:5])
            return {"action": "continue", "message": (
                f"trust-ladder: verified task(s) {names} have not passed their verifier. Run "
                "trust_ladder_task with action=verify for each and fix any failure before you report done.")}
        except Exception:
            logger.exception("trust-ladder: pre_verify failed")
            return None

    def on_transform_llm_output(self, response_text: str = "", session_id: str = "", **kwargs: Any):
        try:
            text = response_text if isinstance(response_text, str) else kwargs.get("assistant_response")
            if not isinstance(text, str) or not text or not DONE_CLAIM.search(text):
                return None
            pending = [t for t in store.load_tasks() if t.get("status") != "passed"
                       and t.get("session_id") and t.get("session_id") == session_id]
            if not pending:
                return None
            store.append_ledger({"type": "unverified_done", "tasks": [t["id"] for t in pending],
                                 "session": session_id})
            names = "; ".join(f"{t['id']} '{t['name']}' ({t.get('status', 'open')}, verifier `{t['verifier']}`)"
                              for t in pending[:5])
            return text + f"\n\n---\ntrust-ladder: NOT VERIFIED. {names} has not passed its verifier yet."
        except Exception:
            logger.exception("trust-ladder: transform_llm_output failed")
            return None

    # ------------------------------------------------------------------ verified tasks

    def create_task(self, name: str, verifier: str, session_id: str = "", source: str = "model") -> dict:
        name = " ".join(str(name or "").split())[:80]
        verifier = str(verifier or "").strip()
        if not name or not verifier:
            return {"ok": False, "error": "a task needs a name and a verifier command"}
        if len(verifier) > 2000:
            return {"ok": False, "error": "verifier command is too long"}
        refusal = self.verifier_refusal(verifier, allow_review=(source == "user"))
        if refusal:
            return {"ok": False, "error": refusal}
        existing = store.find_task(name)
        if existing is not None and existing.get("status") != "passed":
            if existing.get("verifier") != verifier and source == "model":
                return {"ok": False, "error": (
                    f"task {existing['id']} '{existing['name']}' already has the verifier `{existing['verifier']}`. "
                    "Verifiers are fixed once set; only the user can change one (/verify).")}
        if existing is None and len(store.load_tasks()) >= MAX_TASKS:
            return {"ok": False, "error": f"task limit reached ({MAX_TASKS}); ask the user to drop old ones"}
        task = {"name": name, "verifier": verifier, "status": "open", "attempts": 0,
                "session_id": session_id, "source": source}
        if existing is not None:
            task["id"] = existing["id"]
        saved = store.upsert_task(task)
        return {"ok": True, "task": saved}

    def verifier_refusal(self, command: str, *, allow_review: bool = False) -> str:
        """Why trust-ladder will not run *command* as a verifier ('' = fine)."""
        for rule in self.rules():
            if not rule.get("enabled", True) or rule.get("rung") not in ("guard", "review"):
                continue
            if rule["rung"] == "review" and allow_review:
                continue
            try:
                hit = compiler.evaluate_match(rule.get("match") or {}, "terminal", {"command": command})
            except Exception:
                continue
            if hit:
                what = "is blocked by" if rule["rung"] == "guard" else "needs the user's approval under"
                return (f"verifier `{command[:120]}` {what} trust-ladder rule {rule['id']} "
                        f"(\"{rule['text'][:120]}\"); it was not run.")
        return ""

    def run_verifier(self, ref: str, *, timeout: int = 300, task_id: str = "", session_id: str = "",
                     dispatch: Optional[Callable[..., str]] = None, source: str = "model") -> dict:
        task = store.find_task(ref)
        if task is None:
            return {"ok": False, "error": f"no verified task named {ref!r}"}
        # ctx.dispatch_tool goes straight to the terminal tool (its approvals apply) but skips
        # pre_tool_call hooks, so apply this plugin's own guards here: a verifier must never be a
        # way around a correction.
        refusal = self.verifier_refusal(task["verifier"], allow_review=(source == "user"))
        if refusal:
            store.append_ledger({"type": "verify_refused", "task": task["id"], "evidence": redact(refusal),
                                 "session": session_id})
            return {"ok": False, "error": refusal}
        timeout = _int(timeout, 300, lo=5, hi=MAX_VERIFY_TIMEOUT)
        dispatch = dispatch or self._dispatch_terminal
        started = time.time()
        try:
            raw = dispatch(task["verifier"], timeout=timeout, task_id=task_id)
        except Exception as exc:  # dispatch errors become a failed run, never an exception
            raw = {"exit_code": None, "error": f"could not run the verifier: {exc}"}
        exit_code, output, error = _parse_terminal(raw)
        passed = exit_code == 0 and not error
        tail = (output or error or "")[-1500:]
        # Re-read the task under the lock and write only the result fields: the user may have
        # replaced the verifier while this run was in flight (TLC path in docs/statechart,
        # VerifiedTask.tla). A stale run never passes the task and never restores its old verifier.
        saved, counted = store.record_verifier_result(task["id"], task["verifier"], {
            "status": "passed" if passed else "failed", "last_run": time.time(),
            "last_exit": exit_code, "last_output": tail})
        if saved is None:
            return {"ok": False, "error": f"task {task['id']} was dropped while its verifier ran"}
        if not counted:
            passed = False
        store.append_ledger({"type": ("verify_pass" if passed else "verify_fail") if counted else "verify_stale",
                             "task": task["id"], "exit_code": exit_code,
                             "seconds": round(time.time() - started, 1), "session": session_id})
        next_step = ("Verified. You may report this task done." if passed else
                     "Not verified. Fix the failure and run verify again; do not report done.")
        if not counted:
            next_step = (f"The verifier was changed to `{saved['verifier']}` while this run was in flight, "
                         "so this result does not count. Run verify again.")
        return {"ok": True, "passed": passed, "task": task["id"], "name": task["name"], "exit_code": exit_code,
                "output_tail": tail, "next": next_step}

    def _dispatch_terminal(self, command: str, *, timeout: int, task_id: str = "") -> str:
        if self.ctx is None:
            return json.dumps({"exit_code": -1, "error": "no Hermes context to run the verifier"})
        kwargs = {"task_id": task_id} if task_id else {}
        return self.ctx.dispatch_tool("terminal", {"command": command, "timeout": timeout}, **kwargs)

    # ------------------------------------------------------------------ tool handlers

    def tool_learn(self, args: dict, **kwargs: Any) -> str:
        try:
            args = args or {}
            outcome = self.learn(
                args.get("rule", ""), source="model", guard=args.get("guard"), check=args.get("check"),
                review=bool(args.get("review")), fix=args.get("fix", ""), bad_example=args.get("bad_example"),
                good_example=args.get("good_example"),
                session_id=str(kwargs.get("session_id") or kwargs.get("task_id") or ""))
            if not outcome.get("ok"):
                return json.dumps({"error": outcome.get("error", "could not learn the rule")})
            rule = outcome["rule"]
            return json.dumps({
                "ok": True, "rule_id": rule["id"], "rung": compiler.RUNG_LABELS[outcome["rung"]],
                "enforced_as": _describe_match(rule.get("match") or {}) or _describe_check(rule.get("check") or {})
                or "reminder injected every turn",
                "why": outcome["why"], "problems": outcome["problems"],
                "tell_user": f"Learned {rule['id']} at rung {compiler.RUNG_LABELS[outcome['rung']].split('(')[0].strip()}.",
            })
        except Exception as exc:  # tool handlers must return JSON, never raise
            logger.exception("trust-ladder: learn failed")
            return json.dumps({"error": f"trust-ladder could not learn the rule: {exc}"})

    def tool_task(self, args: dict, **kwargs: Any) -> str:
        try:
            args = args or {}
            action = args.get("action")
            session = str(kwargs.get("session_id") or kwargs.get("task_id") or "")
            if action == "create":
                return json.dumps(self.create_task(args.get("task", ""), args.get("verifier", ""), session))
            if action == "verify":
                return json.dumps(self.run_verifier(args.get("task", ""), timeout=args.get("timeout") or 300,
                                                    task_id=str(kwargs.get("task_id") or ""), session_id=session))
            if action == "list":
                return json.dumps({"tasks": [{k: t.get(k) for k in ("id", "name", "verifier", "status", "attempts")}
                                             for t in store.load_tasks()]})
            return json.dumps({"error": "action must be create, verify or list"})
        except Exception as exc:
            logger.exception("trust-ladder: task tool failed")
            return json.dumps({"error": f"trust-ladder task failed: {exc}"})

    # ------------------------------------------------------------------ commands (shared by slash + CLI)

    def cmd_teach(self, raw: str, session_id: str = "") -> str:
        text = (raw or "").strip()
        if not text:
            return ("Usage: /teach <correction>\n"
                    "Examples:\n"
                    "  /teach never force-push; use --force-with-lease\n"
                    "  /teach don't edit .env\n"
                    "  /teach use pnpm instead of npm\n"
                    "  /teach src/**/*.py must not contain breakpoint()\n"
                    "  /teach ask me before running `terraform apply`\n"
                    "  /teach never run /curl[^|]*\\|\\s*(ba)?sh/")
        outcome = self.learn(text, source="teach", session_id=session_id)
        if not outcome.get("ok"):
            return f"trust-ladder: {outcome.get('error')}"
        rule = outcome["rule"]
        how = _describe_match(rule.get("match") or {}) or _describe_check(rule.get("check") or {})
        lines = [f"Learned {rule['id']} -> rung {compiler.RUNG_LABELS[outcome['rung']]}",
                 f"  \"{rule['text']}\""]
        if how:
            lines.append(f"  enforced as: {how}")
        if rule.get("fix"):
            lines.append(f"  fix shown on block: {rule['fix']}")
        lines.append(f"  {outcome['why']}")
        for p in outcome["problems"]:
            lines.append(f"  note: {p}")
        return "\n".join(lines)

    def cmd_trust(self, raw: str) -> str:
        argv = _split(raw)
        sub = argv[0].lower() if argv else "status"
        rest = argv[1:]
        if sub in ("status", "rules", ""):
            return self.render_status()
        if sub in ("log", "ledger", "catches"):
            n = int(rest[0]) if rest and rest[0].isdigit() else 20
            return self.render_log(n)
        if sub in ("disable", "enable", "remove", "rm", "delete") and rest:
            rid = rest[0]
            if sub in ("remove", "rm", "delete"):
                ok = store.remove_rule(rid)
            else:
                ok = store.update_rule(rid, enabled=(sub == "enable")) is not None
            self.invalidate()
            if ok:
                store.append_ledger({"type": f"rule_{sub}", "rule": rid})
            return f"{'Done' if ok else 'No such rule'}: {sub} {rid}"
        if sub in ("observe", "enforce"):
            if self.ctx is None:
                return "Mode can only be changed inside Hermes."
            self.ctx.set_config("mode", sub)
            return (f"trust-ladder mode: {sub}" +
                    (" (guards log what they would block but let calls through)" if sub == "observe" else ""))
        if sub == "test" and rest:
            return self.cmd_test(rest)
        return ("Usage: /trust [status | log [n] | disable <id> | enable <id> | remove <id> | "
                "observe | enforce | test <tool> <json-args>]")

    def cmd_test(self, rest: list[str]) -> str:
        try:
            return self._cmd_test(rest)
        except Exception as exc:
            return f"trust-ladder: test failed: {exc}"

    def _cmd_test(self, rest: list[str]) -> str:
        if not rest:
            return "Usage: /trust test <tool> <json-args | command>"
        tool = rest[0]
        payload = " ".join(rest[1:]) or "{}"
        try:
            args = json.loads(payload)
        except ValueError:
            args = None
        if not isinstance(args, dict):
            field = compiler.COMMAND_FIELDS.get(tool, ("path",))[0]
            args = {field: payload}
        hits = []
        for rule in self.rules():
            if not rule.get("enabled", True):
                continue
            try:
                if rule.get("rung") in ("guard", "review"):
                    ev = compiler.evaluate_match(rule.get("match") or {}, tool, args)
                    if ev:
                        hits.append(f"{rule['id']} ({rule['rung']}) would fire: {redact(ev)}")
                elif rule.get("rung") == "check":
                    ev = compiler.evaluate_check(rule.get("check") or {}, tool, args, "")
                    if ev:
                        hits.append(f"{rule['id']} (check) would fail: {redact(ev)}")
            except Exception as exc:
                hits.append(f"{rule['id']} could not be evaluated: {exc}")
        return "\n".join(hits) if hits else f"No rule fires for {tool} {json.dumps(args)}"

    def cmd_verify(self, raw: str, session_id: str = "", task_id: str = "") -> str:
        text = (raw or "").strip()
        if not text or text in ("list", "ls"):
            tasks = store.load_tasks()
            if not tasks:
                return ("No verified tasks. Add one: /verify <name> -- <command>\n"
                        "  e.g. /verify tests -- pytest -q")
            return "\n".join(f"{t['id']} {t['name']}: {t.get('status', 'open')} "
                             f"({t.get('attempts', 0)} runs) `{t['verifier']}`" for t in tasks)
        if " -- " in f" {text} ":
            name, _, command = text.partition("--")
            outcome = self.create_task(name.strip(), command.strip(), session_id=session_id, source="user")
            if not outcome.get("ok"):
                return f"trust-ladder: {outcome['error']}"
            t = outcome["task"]
            return (f"Verified task {t['id']} '{t['name']}' -> `{t['verifier']}`. The agent is told not to "
                    "report it done until this passes.")
        argv = _split(text)
        if argv and argv[0] in ("run", "check") and len(argv) > 1:
            res = self.run_verifier(" ".join(argv[1:]), session_id=session_id, task_id=task_id, source="user")
            if not res.get("ok"):
                return f"trust-ladder: {res['error']}"
            return (f"{res['task']} {res['name']}: {'PASSED' if res['passed'] else 'FAILED'} "
                    f"(exit {res['exit_code']})\n{res['output_tail'][-600:]}")
        if argv and argv[0] in ("drop", "remove", "rm") and len(argv) > 1:
            ok = store.remove_task(" ".join(argv[1:]))
            return "Dropped." if ok else "No such task."
        return "Usage: /verify <name> -- <command> | /verify list | /verify run <name> | /verify drop <name>"

    # ------------------------------------------------------------------ rendering

    def render_status(self) -> str:
        rules = self.rules()
        ledger = store.read_ledger()
        catches = [e for e in ledger if e.get("type") in ("block", "check_failed", "review",
                                                         "review_blocked_unattended")]
        mode = "" if self.enforcing() else "  [OBSERVE MODE: guards do not block]"
        head = f"trust-ladder: {len(rules)} rule{'s' if len(rules) != 1 else ''}, {len(catches)} catch" \
               f"{'es' if len(catches) != 1 else ''}{mode}"
        out = [head, ""]
        if not rules:
            out.append("No rules yet. Correct the agent, or teach one: /teach never force-push")
        for rung in compiler.RUNG_ORDER:
            group = [r for r in rules if r.get("rung") == rung]
            if not group:
                continue
            out.append(f"RUNG {compiler.RUNG_LABELS[rung]}")
            for r in group:
                state = "on " if r.get("enabled", True) else "off"
                caught = f"caught {r.get('catches', 0)}x" + (f", last {_fmt_time(r['last_caught'])}"
                                                            if r.get("last_caught") else "")
                out.append(f"  {r['id']:<4} [{state}] {r['text'][:70]}")
                how = _describe_match(r.get("match") or {}) or _describe_check(r.get("check") or {})
                detail = f"       {how[:90]}" if how else "       reminder only"
                out.append(f"{detail}  |  {caught}  |  from {r.get('source', '?')}")
            out.append("")
        tasks = store.load_tasks()
        if tasks:
            out.append("VERIFIED TASKS")
            for t in tasks:
                out.append(f"  {t['id']:<4} {t['name'][:40]}: {t.get('status', 'open').upper()} "
                           f"after {t.get('attempts', 0)} run(s)  `{t['verifier'][:60]}`")
            out.append("")
        recent = catches[-5:]
        if recent:
            out.append("RECENT CATCHES")
            for e in reversed(recent):
                out.append(f"  {_fmt_time(e.get('ts'))}  {e.get('rule', '?'):<4} {e.get('type'):<12} "
                           f"{e.get('tool', '')}: {str(e.get('evidence', ''))[:70]}")
        return "\n".join(out).rstrip()

    def render_log(self, n: int = 20) -> str:
        events = store.read_ledger(limit=max(1, min(n, 500)))
        if not events:
            return "Ledger is empty."
        lines = []
        for e in events:
            subject = e.get("rule") or e.get("task") or ""
            detail = e.get("evidence") or e.get("rung") or e.get("exit_code", "")
            lines.append(f"{_fmt_time(e.get('ts'))}  {e.get('type', '?'):<14} {subject:<4} "
                         f"{e.get('tool', '')} {str(detail)[:80]}".rstrip())
        return "\n".join(lines)


def _int(value: Any, default: int, *, lo: int, hi: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(lo, min(number, hi))


def _split(raw: str) -> list[str]:
    try:
        return shlex.split(raw or "")
    except ValueError:
        return (raw or "").split()


def _parse_terminal(raw: Any) -> tuple[Optional[int], str, str]:
    data: Any = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            return None, raw, ""
    if not isinstance(data, dict):
        return None, str(raw), ""
    code = data.get("exit_code")
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None
    return code, str(data.get("output") or ""), str(data.get("error") or "")
