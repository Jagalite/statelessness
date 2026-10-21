import Assurance

open Statelessness.Assurance

/-! Executable, independent model of the documented conformance-fixture protocol.
The theorems are in Assurance.lean; this adapter is tested, not itself verified.
-/
namespace Bridge

def bit (n i : Nat) : Bool := decide (n / 2 ^ i % 2 = 1)
def flag (b : Bool) : Nat := if b then 1 else 0
def numbers (ns : List Nat) : String := String.intercalate " " (ns.map toString)
def values (c : Counts) : List Nat := [c.passed, c.failed, c.skipped, c.missing]
def statusCode : Status → Nat
  | .passed => 0 | .failed => 1 | .skipped => 2 | .missing => 3

def outcome : Nat → SearchOutcome
  | 0 => .graphExhausted | 1 => .casesCompleted | 2 => .depthLimited
  | 3 => .budgetLimited | 4 => .interrupted | 5 => .timedOut
  | 6 => .failed | _ => .notStarted

structure Batch where
  target : Counts := {}
  extra : Counts := {}
  duplicate : Bool := false

def batch (required : Bool) (code : Nat) : Batch :=
  let omitted : Counts := if required then ⟨0, 0, 0, 1⟩ else {}
  match code with
  | 0 => ⟨omitted, {}, false⟩
  | 1 => ⟨⟨1, 0, 0, 0⟩, {}, false⟩
  | 2 => ⟨⟨0, 1, 0, 0⟩, {}, false⟩
  | 3 => ⟨⟨0, 0, 1, 0⟩, {}, false⟩
  | 4 => ⟨⟨2, 0, 0, 0⟩, {}, true⟩
  | 5 => ⟨omitted, ⟨1, 0, 0, 0⟩, false⟩
  | 6 => ⟨omitted, ⟨0, 1, 0, 0⟩, false⟩
  | 7 => ⟨omitted, ⟨0, 0, 1, 0⟩, false⟩
  | 8 => ⟨⟨1, 0, 0, 0⟩, ⟨0, 1, 0, 0⟩, false⟩
  | _ => ⟨⟨1, 0, 0, 0⟩, ⟨0, 0, 1, 0⟩, false⟩

structure Observations where
  state : Counts := {}
  transition : Counts := {}
  extra : Counts := {}
  duplicate : Bool := false
  edges : Nat := 0

def stateBatch (o : Observations) (required : Bool) (code : Nat) : Observations :=
  let b := batch required code
  { o with state := merge o.state b.target, extra := merge o.extra b.extra,
           duplicate := o.duplicate || b.duplicate }

def transitionBatch (o : Observations) (required : Bool) (code : Nat) : Observations :=
  let b := batch required code
  { o with transition := merge o.transition b.target, extra := merge o.extra b.extra,
           duplicate := o.duplicate || b.duplicate, edges := satAdd o.edges 1 }

def turns (rs rt : Bool) (o : Observations) : List Nat → Observations
  | s :: t :: rest => turns rs rt (transitionBatch (stateBatch o rs s) rt t) rest
  | _ => o

def observationCase (ns : List Nat) : Except String String := do
  match ns with
  | mask :: family :: goal :: req :: out :: hits :: err :: tamper :: initial :: tail =>
    if !(decide (mask ≤ 3 ∧ family ≤ 1 ∧ goal ≤ 2 ∧ req ≤ 1 ∧ out ≤ 7 ∧
                  hits ≤ 31 ∧ err ≤ 1 ∧ tamper ≤ 12 ∧ tail.length % 2 = 0) &&
         (initial :: tail).all (fun n => decide (n ≤ 9))) then
      throw "invalid R protocol"
    if tamper ≠ 0 then
      if [mask, family, goal, req, out, hits, err] ≠ [3, 1, 1, 0, 0, 5, 0] ∨
          initial ≠ 1 ∨ tail ≠ [1, 1] then
        throw "tamper cases require the documented good baseline"
    let rs := bit mask 0
    let rt := bit mask 1
    let o := turns rs rt (stateBatch {} rs initial) tail
    let edges := decide (0 < o.edges)
    let fam := decide (family = 1)
    let hasGoal := decide (goal ≠ 0)
    let fail := decide (err = 1)
    let inputSeen := edges && !fail && fam && bit hits 0
    let outsideSeen := edges && !fail && bit hits 1
    let goalSeen := edges && !fail && hasGoal && bit hits 2
    let errors := o.duplicate || (edges && (fail || bit hits 3 || bit hits 4 ||
      (bit hits 0 && !fam) || (bit hits 2 && !hasGoal)))
    let unclassified := if edges && (fail || !((fam && bit hits 0) || bit hits 1))
      then o.edges else 0
    let valid := decide (tamper = 0)
    let evidence : Evidence := {
      valid := valid
      required := (if rs then [o.state] else []) ++ (if rt then [o.transition] else [])
      observed := [o.state, o.transition, o.extra]
      hasRequiredFamily := fam
      familiesSatisfied := (!fam || inputSeen) && !outsideSeen
      goalsSatisfied := !hasGoal || goalSeen
      callbackError := errors
      unclassified := decide (0 < unclassified)
      requirement := if req = 0 then .graph else .completed
      outcome := outcome out
    }
    return numbers (values o.state ++ values o.transition ++
      [flag valid, flag (strict evidence), flag errors, unclassified])
  | _ => throw "invalid R protocol arity"

def respond (line : String) : Except String String := do
  let words := ((line.replace "\n" "").replace "\r" "").splitOn " "
  match words with
  | mode :: args =>
    let ns ← args.mapM fun s => match s.toNat? with
      | some n => Except.ok n
      | none => Except.error "invalid natural number"
    if !(ns.all (fun n => decide (n ≤ maxCount))) then throw "number exceeds u64"
    match mode, ns with
    | "C", [p, f, s, m, candidate] =>
      if candidate > 3 then throw "invalid candidate status"
      let c : Counts := ⟨p, f, s, m⟩
      let valid := decide (statusCode (status c) = candidate)
      return numbers [flag valid, flag (valid && decide (status c = .passed))]
    | "M", [p, f, s, m, p', f', s', m'] =>
      let c := merge ⟨p, f, s, m⟩ ⟨p', f', s', m'⟩
      return numbers (values c ++ [statusCode (status c), flag (decide (status c = .passed))])
    | "R", _ => observationCase ns
    | _, _ => throw "invalid protocol command or arity"
  | _ => throw "empty protocol line"

end Bridge

def main : IO Unit := do
  let stdin ← IO.getStdin
  let stdout ← IO.getStdout
  repeat
    let line ← stdin.getLine
    if line.isEmpty then break
    match Bridge.respond line with
    | .ok answer => stdout.putStrLn answer
    | .error message => throw (IO.userError message)
