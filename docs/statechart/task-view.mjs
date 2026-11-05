// Reader's labels for VerifiedTask.tla (trust-ladder verified tasks).
export const title = "trust-ladder: a verified task";
export const state = (v) => {
  if (!v.task.present) return "No task";
  const who = v.task.verifier === "real" ? "your verifier" : "agent's verifier";
  const status = { open: "Open", failed: "Failed", passed: "Passed" }[v.task.status];
  return `${status}, ${who}${v.run.active ? ", verifier running" : ""}`;
};
export const group = (v) => (!v.task.present ? "none" : v.task.status === "passed" ? "done" : "working");
export const colors = { none: "blue", working: "orange", done: "teal" };
export const step = (action, from, to) => {
  switch (action) {
    case "AgentCreatesTask": return "agent creates the task";
    case "UserSetsRealVerifier": return "you set the real verifier";
    case "VerifyStarts": return "agent runs verify";
    case "VerifierExits":
      if (from.run.snap.verifier !== from.task.verifier && to.task.verifier === from.task.verifier &&
          to.task.status === from.task.status) return "result ignored [verifier was replaced]";
      return to.task.status === "passed" ? "verifier exits 0" : "verifier fails";
    case "AgentRepliesDone": return null; // a reply does not change the task; UnverifiedDoneIsFlagged checks it
    default: return null;
  }
};
