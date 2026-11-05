# Changelog

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
