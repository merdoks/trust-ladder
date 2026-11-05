---- MODULE RuleLadder ----
(* `last` is the outcome of the latest matching call since the last change.
   Rule lifecycle of trust-ladder: taught -> compiled at a rung -> active -> catches, disabled,
   removed. Two processes share one Hermes profile: the agent's (hooks) and the user's
   (/trust or `hermes trust-ladder` from another shell). FIXED = FALSE is v0.1.0 as first built
   (mtime-keyed rules cache, ids = highest live id + 1); FIXED = TRUE is the shipped code
   (rules read on every call, persisted id counter). A call that arrives is matched by every
   rule, so the outcome depends only on the rungs, the enabled flags and the mode. *)
EXTENDS Naturals
CONSTANTS FIXED, MaxIds, MaxLive
Rungs == {"guard", "review", "rule"}
NoRule == [id |-> 0, rung |-> "rule", enabled |-> FALSE, valid |-> FALSE]

VARIABLES disk, cache, stamp, cstamp, dirty, used, reused, nextId, mode, last
vars == <<disk, cache, stamp, cstamp, dirty, used, reused, nextId, mode, last>>

Ids(S) == {r.id : r \in S}
RECURSIVE Count(_)
Count(S) == IF S = {} THEN 0 ELSE LET x == CHOOSE y \in S : TRUE IN 1 + Count(S \ {x})
Max(S) == IF S = {} THEN 0 ELSE CHOOSE x \in S : \A y \in S : y <= x
NoCall == [outcome |-> "none", by |-> 0, activeGuard |-> FALSE, blockerEnabled |-> TRUE, mode |-> "enforce"]

Init == /\ disk = {} /\ cache = {} /\ stamp = 1 /\ cstamp = 0 /\ dirty = FALSE
        /\ used = {} /\ reused = FALSE /\ nextId = 1 /\ mode = "enforce" /\ last = NoCall

Write(new) == /\ disk' = new /\ dirty' = ~FIXED /\ UNCHANGED stamp

View == IF FIXED THEN disk ELSE IF cstamp = stamp THEN cache ELSE disk

YouTeach(rung) ==
  /\ Count(disk) < MaxLive
  /\ LET id == IF FIXED THEN nextId ELSE Max(Ids(disk)) + 1
     IN /\ id <= MaxIds
        /\ Write(disk \cup {[id |-> id, rung |-> rung, enabled |-> TRUE, valid |-> TRUE]})
        /\ used' = used \cup {id}
        /\ reused' = (reused \/ id \in used)
        /\ nextId' = IF FIXED THEN nextId + 1 ELSE nextId
  /\ last' = NoCall
  /\ UNCHANGED <<cache, cstamp, mode>>

Set(r, f) == Write((disk \ {r}) \cup {f})
Keep == last' = NoCall /\ UNCHANGED <<cache, cstamp, used, reused, nextId, mode>>
YouDisable == \E r \in disk : r.enabled /\ Set(r, [r EXCEPT !.enabled = FALSE]) /\ Keep
YouEnable == \E r \in disk : ~r.enabled /\ Set(r, [r EXCEPT !.enabled = TRUE]) /\ Keep
YouRemove == \E r \in disk : Write(disk \ {r}) /\ Keep
FileGetsCorrupted == \E r \in disk : r.valid /\ Set(r, [r EXCEPT !.valid = FALSE]) /\ Keep
YouToggleMode ==
  /\ mode' = (IF mode = "enforce" THEN "observe" ELSE "enforce")
  /\ last' = NoCall
  /\ UNCHANGED <<disk, cache, stamp, cstamp, dirty, used, reused, nextId>>
ClockTicks ==
  /\ dirty
  /\ stamp' = stamp + 1
  /\ dirty' = FALSE
  /\ UNCHANGED <<disk, cache, cstamp, used, reused, nextId, mode, last>>

ACallArrives ==
  LET seen == View
      guards == {r \in seen : r.rung = "guard" /\ r.enabled /\ r.valid}
      reviews == {r \in seen : r.rung = "review" /\ r.enabled /\ r.valid}
      blocker == IF guards = {} THEN NoRule ELSE CHOOSE g \in guards : \A h \in guards : g.id <= h.id
      truth == \E r \in disk : r.rung = "guard" /\ r.enabled /\ r.valid
      onDisk(id) == \E r \in disk : r.id = id /\ r.enabled
  IN /\ last' = [outcome |-> IF guards # {} /\ mode = "enforce" THEN "blocked"
                             ELSE IF reviews # {} THEN "asks Hermes" ELSE "ran",
                 by |-> IF mode = "enforce" THEN blocker.id ELSE 0,
                 activeGuard |-> truth /\ mode = "enforce",
                 blockerEnabled |-> blocker.id = 0 \/ onDisk(blocker.id),
                 mode |-> mode]
     /\ cache' = IF FIXED THEN cache ELSE seen
     /\ cstamp' = IF FIXED THEN cstamp ELSE stamp
     /\ UNCHANGED <<disk, stamp, dirty, used, reused, nextId, mode>>

YouTeachGuard == YouTeach("guard")
YouTeachReview == YouTeach("review")
YouTeachRule == YouTeach("rule")

Next == \/ YouTeachGuard \/ YouTeachReview \/ YouTeachRule
        \/ YouDisable \/ YouEnable \/ YouRemove \/ FileGetsCorrupted
        \/ YouToggleMode \/ ClockTicks \/ ACallArrives

Spec == Init /\ [][Next]_vars

ActiveGuardBlocks == last.activeGuard => last.outcome = "blocked"
DisabledRuleNeverBlocks == last.outcome = "blocked" => last.blockerEnabled
ObserveNeverBlocks == last.mode = "observe" => last.outcome # "blocked"
RuleIdsNeverReused == ~reused
Constraint == stamp <= 4
====
