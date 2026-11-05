---- MODULE VerifiedTask ----
(* Verified task lifecycle: open -> verifying -> passed only after its verifier exits 0.
   The agent creates a task with an easy verifier and runs verify; while a run is in flight the
   user may replace the verifier with the real one (/verify <name> -- <cmd>). A "done" reply must
   carry NOT VERIFIED unless the task passed. FIXED = FALSE is v0.1.0 as first built (the run
   writes back the task copy it read before the run); FIXED = TRUE is the shipped code (re-read
   under the lock, result fields only, a run of a replaced verifier does not count). *)

EXTENDS Naturals
CONSTANTS FIXED
Cmds == {"easy", "real"}            
Passes(c) == c = "easy"              

VARIABLES task, run, reply, realSet
vars == <<task, run, reply, realSet>>
NoTask == [present |-> FALSE, verifier |-> "easy", status |-> "open", passedWith |-> "none"]

Init == task = NoTask /\ run = [active |-> FALSE, snap |-> NoTask] /\ reply = [said |-> "nothing", passed |-> FALSE]
        /\ realSet = FALSE

AgentCreatesTask == 
  /\ ~task.present
  /\ task' = [present |-> TRUE, verifier |-> "easy", status |-> "open", passedWith |-> "none"]
  /\ UNCHANGED <<run, reply, realSet>>

UserSetsRealVerifier == 
  /\ task.present /\ task.verifier # "real" /\ task.status # "passed"
  /\ task' = [task EXCEPT !.verifier = "real", !.status = "open", !.passedWith = "none"]
  /\ realSet' = TRUE
  /\ UNCHANGED <<run, reply>>

VerifyStarts == 
  /\ task.present /\ ~run.active
  /\ run' = [active |-> TRUE, snap |-> task] /\ UNCHANGED <<task, reply, realSet>>

VerifierExits == 
  /\ run.active
  /\ LET ok == Passes(run.snap.verifier)
         stale == run.snap.verifier # task.verifier
     IN task' = IF FIXED
                THEN 
                     
                     IF stale THEN task
                     ELSE [task EXCEPT !.status = IF ok THEN "passed" ELSE "failed",
                                       !.passedWith = IF ok THEN run.snap.verifier ELSE "none"]
                ELSE 
                     
                     [run.snap EXCEPT !.status = IF ok THEN "passed" ELSE "failed",
                                      !.passedWith = IF ok THEN run.snap.verifier ELSE "none"]
  /\ run' = [active |-> FALSE, snap |-> NoTask]
  /\ UNCHANGED <<reply, realSet>>

AgentRepliesDone == 
  /\ task.present
  /\ reply' = [said |-> IF task.status = "passed" THEN "done" ELSE "done + NOT VERIFIED",
                passed |-> task.status = "passed"]
  /\ UNCHANGED <<task, run, realSet>>

Done == task.status = "passed" /\ ~run.active /\ UNCHANGED vars

Next == AgentCreatesTask \/ UserSetsRealVerifier \/ VerifyStarts \/ VerifierExits \/ AgentRepliesDone \/ Done
Spec == Init /\ [][Next]_vars

PassedOnlyWithPassingVerifier == task.status = "passed" => (task.passedWith = task.verifier /\ Passes(task.verifier))
UserVerifierNeverReverted == realSet => task.verifier = "real"
UnverifiedDoneIsFlagged == reply.said = "done" => reply.passed
====
