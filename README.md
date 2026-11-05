# 🪜 trust-ladder

**Correct your Hermes agent once, and it stays corrected.**

When you tell an agent "never push straight to main", the correction usually lives in a prompt or
a memory note, and the agent forgets it three sessions later. trust-ladder turns the correction into
something the agent *cannot* repeat: a deterministic guard that Hermes checks before every tool
call, with a ledger that shows you each time it saved you.

The idea comes from [@poteto's "fix ladder"](https://x.com/poteto/status/2102050467505430555): when you
correct an agent, fix it at the highest rung you can reach.

| Rung | What trust-ladder installs | Hermes surface |
|---|---|---|
| **1 · impossible** | A guard (regex / path glob / tool matcher) that **blocks** the call before it runs, telling the model why and what to do instead | `pre_tool_call` → `block` |
| **2 · checked** | A check on what a write puts into matching files (or on a tool's output); a failure is appended to the tool result so the model fixes it at once | `transform_tool_result` |
| **3 · rule** | Guidance re-injected every turn when no matcher can express the correction | `pre_llm_call` |
| **4 · human review** | "Ask me before X": matching calls go to Hermes's own approval prompt | `pre_tool_call` → `approve` |

A prompt or skill can *ask* the model to remember. trust-ladder *enforces* it at the tool-call boundary,
proves each guard before installing it, and keeps score.

## What it does

- **Capture corrections two ways.** Type `/teach never push straight to main`, or just correct the agent
  in chat: trust-ladder notices correction-shaped messages and nudges the model to call its
  `trust_ladder_learn` tool with the rule, a matcher and the mistaken call.
- **Compile to the highest provable rung.** A deterministic parser handles common phrasings
  ("never run `X`", "don't edit `.env`", "use pnpm instead of npm", "`src/**/*.py` must not contain
  `breakpoint()`", "ask me before running `terraform apply`", known idioms such as force-push,
  `rm -rf`, `--no-verify`, push to main, and explicit `/regex/`). Anything it can't express stays a
  rule and the model is invited to promote it.
- **Verified compilation.** A guard from the model is installed only if it **matches the mistaken call
  (`bad_example`) and spares the corrected one (`good_example`)**. Otherwise it falls back a rung and
  says why. Catch-all patterns (`.*`, `**/*`) and guards on trust-ladder's own tools are refused.
- **Guards can't stall the agent.** Python regexes have no timeout, and a natural pattern like
  `git.*push.*--force` takes ~7 s on a long command. Every pattern is profiled on adversarial inputs
  before it is installed: cheap ones run as-is, slow-but-sane ones are searched in bounded overlapping
  windows, and hopeless ones (`a.*b.*c.*x`) are refused with the reason.
- **Verified tasks.** `/verify tests -- pytest -q` (or the `trust_ladder_task` tool) attaches a verifier
  command to a task. Until it exits 0: the task is listed in every turn, the `pre_verify` hook keeps a
  code-editing turn going, and a "done" reply gets a **NOT VERIFIED** footer. The model can create a
  task but cannot swap its verifier for an easier one, and a verifier can never smuggle in a command
  one of your guards blocks (it is checked against your rules before it runs).
- **Ledger and status.** Every block, failed check, review and verifier run is logged. `/trust` shows
  rules grouped by rung with catch counts and the latest catches; a one-line notice appears in the
  chat whenever a guard blocks something.
- **Model can add, never remove.** Only you can `/trust disable|enable|remove <id>`.
- **Observe mode.** `/trust observe` logs what guards *would* block without blocking; `/trust enforce`
  turns them back on. `/trust test terminal 'git push origin main'` dry-runs a call against your rules.

## Example

```text
you>   Push my fix to origin
agent> (runs git push origin main)  Pushed your fix to origin/main.
you>   No. Never push straight to main. Push a branch and open a PR.
agent> (calls trust_ladder_learn with a guard, the mistaken call and the corrected call)
       Learned r1 at rung 1: pushes to main are now blocked before they run.

-- a new session, days later --
you>   Ship the follow-up fix
agent> (tries git push origin main)
       BLOCKED by trust-ladder rule r1 (rung 1 guard). This terminal call did not run.
       The user's standing correction: "Never push straight to main. Push a branch and open a PR."
       Do instead: push a branch (git switch -c <name> && git push -u origin <name>) and open a PR
agent> (pushes fix/follow-up instead)
you>   /trust
       RUNG 1 impossible  (guard blocks the call)
         r1   [on ] Never push straight to main. Push a branch and open a PR.
              terminal command =~ /git\s+push.../  |  caught 1x  |  from model
```

More `/teach` one-liners and what they compile to:

| You type | Rung | Enforced as |
|---|---|---|
| `/teach never force-push; use --force-with-lease` | 1 guard | `git push` with `--force`/`-f`/`+ref`, but not `--force-with-lease` |
| `/teach don't edit .env` | 1 guard | writes/patches to `.env`, plus `rm`/`mv`/`cp`/`>` on it in the terminal |
| `/teach use pnpm instead of npm` | 1 guard | `npm …` blocked, fix says "use pnpm" |
| `/teach src/**/*.py must not contain breakpoint()` | 2 check | a write that adds `breakpoint()` gets a FAILED check appended |
| `/teach ask me before running \`terraform apply\`` | 4 review | Hermes's approval prompt |
| `/teach keep PR descriptions short` | 3 rule | reminder every turn |

## Model-checked, not just tested

The two lifecycles that matter (a rule moving up and down the ladder, and a task that may only be marked done after its verifier passes) are modelled in TLA+ and checked with TLC (`docs/statechart/`). The check found four real bugs before release: a stale rules cache across processes, reused rule ids, reused task ids, and an in-flight run of a replaced verifier marking a task passed. Each one is replayed as a test in `tests/test_statechart_replays.py` and fixed by design. With the fixes, every invariant holds (the full rule model explores about 1.56M distinct states). Charts: `docs/statechart/rule-chart.mmd`, `docs/statechart/task-chart.mmd`.

## Install

```bash
hermes plugins install <owner>/trust-ladder --enable      # or copy this folder to ~/.hermes/plugins/
```

Requires Hermes ≥ 0.22.7. No Python dependencies, no API keys.

## Commands

| Command | |
|---|---|
| `/teach <correction>` | Compile and store a correction (`/teach` alone shows examples) |
| `/trust` | Rules by rung, catch counts, verified tasks, recent catches |
| `/trust log [n]` | The last *n* ledger events |
| `/trust disable\|enable\|remove <id>` | Manage a rule (user only) |
| `/trust observe` / `/trust enforce` | Dry-run mode on/off |
| `/trust test <tool> <json-args>` | Which rules would fire for a call |
| `/verify <name> -- <command>` | Attach a verifier to a task |
| `/verify list` · `/verify run <name>` · `/verify drop <name>` | Manage verified tasks |
| `hermes trust-ladder status\|log\|teach\|test\|disable\|enable\|remove` | Same from a terminal (classic in-process plugin mode) |

## Tools the model gets

- `trust_ladder_learn(rule, guard?, check?, review?, fix?, bad_example?, good_example?)`
- `trust_ladder_task(action=create|verify|list, task, verifier?, timeout?)`

## Settings (`plugins.entries.trust-ladder.settings`, also in Desktop → Settings → Plugins)

| Key | Default | |
|---|---|---|
| `mode` | `enforce` | `observe` logs would-be blocks without blocking |
| `inject_rules` | `true` | Remind the agent of rules each turn |
| `correction_nudge` | `true` | Ask the agent to record corrections it receives |
| `notify_catches` | `true` | Post a one-line notice in the chat when a guard blocks a call |
| `max_injected_rules` | `20` | Cap on rules injected per turn |

## Unattended runs and approvals

- Rung 4 hands the call to **Hermes's own approval gate** (`{"action": "approve"}`); trust-ladder never
  approves anything itself and never overrides your approval settings. In a cron job, a `-q` run or a
  webhook, Hermes resolves it instantly from `approvals.cron_mode` / `single_query_mode` /
  `unattended_mode` (default `deny`), so nothing waits for a person.
- Verifier commands run through Hermes's **terminal tool** via `ctx.dispatch_tool`, so dangerous-command
  approvals, redaction and the session's working directory apply exactly as if the model ran them.
  Because that path skips `pre_tool_call` hooks, trust-ladder checks the verifier against your own
  guards first; timeouts are capped at 600 s (Hermes's foreground limit).
- trust-ladder never prompts, never opens a browser and never waits on a person, so it cannot hang a
  cron job or the gateway. Hook errors are caught and logged; a plugin bug lets the call through rather
  than wedging the agent. A rule that cannot be evaluated is skipped and logged, and never switches
  off the rules after it.
- Rules, tasks and the ledger are written atomically under a cross-process lock, so the CLI, the
  gateway and a cron job can share one profile safely; state follows the active Hermes profile.

## Disclosures

- **Local data only.** Rules, verified tasks and the ledger are stored under
  `<HERMES_HOME>/plugin-data/trust-ladder/` (`rules.json`, `tasks.json`, `ledger.jsonl`, trimmed to the
  last 5,000 rows past 2 MB, plus a `.lock` file). The ledger keeps the matched snippet (≤ 200 chars,
  with secret-looking tokens masked), tool name, rule id, session id and time, never full tool
  arguments or results. Verified tasks keep the last 1,500 characters of the verifier's output.
- **No network, no telemetry, no credentials.** The plugin makes no network calls, reads no environment
  variables, keys or credential files, and needs no `get_secret` because it uses no secrets.
- **Shell commands:** only the verifier commands that you (`/verify`) or the agent (`trust_ladder_task`)
  attach, and only through Hermes's terminal tool with normal approvals. The plugin spawns no processes
  of its own and runs no background threads.
- **What it reads:** tool-call arguments (to match guards), the content a write tool is about to write
  (for checks), tool results (for `result_must_not_match` checks), the current user message (to spot
  corrections) and the final reply (to flag unverified "done" claims). It reads no files outside its own
  data directory.
- **What it changes:** it can block a tool call (rung 1), append a FAILED-check note to a tool result
  (rung 2), add a short context block to the user turn with your rules and open verified tasks (capped
  by `max_injected_rules`), ask the agent to keep going once when a verified task has not passed
  (`pre_verify`), and append a NOT VERIFIED footer to a final reply that claims done. It posts a one-line
  chat notice on each block (`notify_catches`).
- **Model-written rules:** the agent can add rules (only additive, only blocking or reminding), so a
  confused or prompt-injected model could over-block; `/trust`, `/trust disable <id>` and `observe`
  mode are the remedy. It can never remove or loosen a rule.
- **Not a sandbox.** Guards are best-effort pattern matches on what a call names. A determined model can
  phrase a command a guard does not recognise (a script file, an alias). Use them to stop repeat
  mistakes, and use OS isolation for hostile code.

## Development

```bash
cd tests && pytest              # 85 tests, no Hermes runtime needed (uses a fake ctx)
hermes plugins doctor . --ci
hermes plugins validate . --install-deps
HERMES_SRC=/path/to/hermes-agent demo/run_demo.sh   # reproducible end-to-end demo (scripted model)
HERMES_SRC=/path/to/hermes-agent demo/record.sh     # asciinema cast -> demo/trust-ladder-demo.mp4 (1080p)
```

`demo/trust-ladder-demo.mp4` (48 s) is that run recorded with asciinema and rendered by
`demo/render.py`. The video shortens Hermes start-up pauses to 3.2 s and tints block notices red;
the terminal text is unedited.

The demo runs real Hermes, the real agent loop, the real terminal tool and real git against a local
bare remote; only the model's choices are scripted (with Hermes's own `tests/fakes/fake_llm_provider.py`),
so it is reproducible offline with no API key.

## License

MIT
