# Changelog

## 0.1.1
- Lenient `trust_ladder_learn` input for weaker models (seen with gpt-oss-20b): a plain-sentence
  `guard` is read as rule text, a bare command string counts as `bad_example`/`good_example`,
  prose examples are ignored instead of downgrading a verified guard, and a missing `rule` falls
  back to the guard sentence. Every adjustment is reported in `problems`.

## 0.1.0 (unreleased)
- First release: `/teach`, `/trust`, `/verify`; `trust_ladder_learn` and `trust_ladder_task` tools;
  `hermes trust-ladder …` CLI (in-process plugin mode).
- Rungs: guard (`pre_tool_call` block), check (`transform_tool_result`), rule (`pre_llm_call`),
  review (Hermes approval gate via `{"action": "approve"}`).
- Verified compilation against bad/good examples; catch-all refusal.
- Regex cost profiling: slow patterns are windowed or refused, so a guard can never stall a tool call.
- Verified tasks with `pre_verify` gating and a NOT VERIFIED footer on unverified "done" replies;
  verifiers are checked against your guards and capped at 600 s.
- Review rules always defer to Hermes's approval gate (honours `approvals.cron_mode` etc.; nothing waits
  in unattended runs).
- Catch ledger with secret masking, observe mode, `/trust test` dry runs, chat notice on blocks.
- Cross-process locking and profile-aware caching; corrupt rules are skipped, never fatal.
