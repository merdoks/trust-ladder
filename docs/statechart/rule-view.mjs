// Reader's labels for RuleLadder.tla (trust-ladder rule lifecycle), drawn with one rule slot.
const RUNG = { guard: "Guard", review: "Review", rule: "Reminder" };
const rulesOf = (v) => (Array.isArray(v.disk) ? v.disk : Object.values(v.disk));
const CALL = { blocked: "last call blocked", "asks Hermes": "last call sent to approval", ran: "last call ran" };
export const title = "trust-ladder: a correction from taught to caught";
export const state = (v) => {
  const [r] = rulesOf(v);
  if (!r) return "No rule";
  if (!r.valid) return "Damaged rule (skipped, others still enforced)";
  if (!r.enabled) return `${RUNG[r.rung]} off`;
  const mode = v.mode === "observe" && r.rung === "guard" ? ", observe mode" : "";
  const call = v.last.outcome !== "none" && r.rung !== "rule" ? ` - ${CALL[v.last.outcome]}` : "";
  return `${RUNG[r.rung]} on${mode}${call}`;
};
export const group = (v) => {
  const [r] = rulesOf(v);
  if (!r) return "none";
  if (!r.valid || !r.enabled) return "inactive";
  return r.rung === "guard" ? "guard" : r.rung === "review" ? "review" : "rule";
};
export const colors = { none: "blue", guard: "teal", review: "purple", rule: "yellow", inactive: "orange" };
export const step = (action, from, to) => {
  if (action === "YouTeachGuard") return "you correct the agent [a matcher proves itself]";
  if (action === "YouTeachReview") return "you say: ask me before X";
  if (action === "YouTeachRule") return "you correct the agent [no matcher fits]";
  if (action === "YouDisable") return "you /trust disable";
  if (action === "YouEnable") return "you /trust enable";
  if (action === "YouRemove") return "you /trust remove";
  if (action === "FileGetsCorrupted") return "rules.json gets damaged";
  if (action === "YouToggleMode") return to.mode === "observe" ? "you /trust observe" : "you /trust enforce";
  if (action === "ACallArrives") return "a matching call arrives";
  return null;
};
