import Std
import Lean.Elab.Tactic.Omega

/-!
A development-only specification of the assurance evidence gates.
This is not a proof of the Rust implementation or of report authenticity.
The Boolean `valid` represents the separate report-validation obligation.
-/
namespace Statelessness.Assurance

structure Counts where
  passed : Nat := 0
  failed : Nat := 0
  skipped : Nat := 0
  missing : Nat := 0
  deriving Repr, DecidableEq

inductive Status where
  | passed | failed | skipped | missing
  deriving Repr, DecidableEq

def Clean (c : Counts) : Prop :=
  c.failed = 0 ∧ c.skipped = 0 ∧ c.missing = 0

instance (c : Counts) : Decidable (Clean c) := inferInstanceAs
  (Decidable (c.failed = 0 ∧ c.skipped = 0 ∧ c.missing = 0))

/-- The precedence used by `property_results` in src/assurance.rs. -/
def status (c : Counts) : Status :=
  if 0 < c.failed then .failed
  else if 0 < c.skipped then .skipped
  else if 0 < c.missing then .missing
  else if 0 < c.passed then .passed
  else .missing

theorem status_passed_iff (c : Counts) :
    status c = .passed ↔ 0 < c.passed ∧ Clean c := by
  unfold status
  split
  · rename_i hf
    constructor
    · intro h; cases h
    · intro h
      have hz := h.2.1
      omega
  · rename_i hf
    split
    · rename_i hs
      constructor
      · intro h; cases h
      · intro h
        have hz := h.2.2.1
        omega
    · rename_i hs
      split
      · rename_i hm
        constructor
        · intro h; cases h
        · intro h
          have hz := h.2.2.2
          omega
      · rename_i hm
        split
        · rename_i hp
          constructor
          · intro _
            exact ⟨hp, by unfold Clean; omega⟩
          · intro _; rfl
        · rename_i hp
          constructor
          · intro h; cases h
          · intro h
            have hz := h.1
            omega

/-- Rust's u64 counter ceiling; using Nat avoids silently assuming no overflow. -/
def maxCount : Nat := 18446744073709551615

def satAdd (a b : Nat) : Nat := min maxCount (a + b)

theorem satAdd_zero_iff (a b : Nat) :
    satAdd a b = 0 ↔ a = 0 ∧ b = 0 := by
  unfold satAdd maxCount
  omega

theorem satAdd_positive_iff (a b : Nat) :
    0 < satAdd a b ↔ 0 < a ∨ 0 < b := by
  unfold satAdd maxCount
  omega

def merge (a b : Counts) : Counts :=
  ⟨satAdd a.passed b.passed, satAdd a.failed b.failed,
   satAdd a.skipped b.skipped, satAdd a.missing b.missing⟩

theorem merge_clean_iff (a b : Counts) :
    Clean (merge a b) ↔ Clean a ∧ Clean b := by
  simp only [Clean, merge, satAdd_zero_iff]
  constructor
  · rintro ⟨⟨af, bf⟩, ⟨as_, bs⟩, ⟨am, bm⟩⟩
    exact ⟨⟨af, as_, am⟩, ⟨bf, bs, bm⟩⟩
  · rintro ⟨⟨af, as_, am⟩, ⟨bf, bs, bm⟩⟩
    exact ⟨⟨af, bf⟩, ⟨as_, bs⟩, ⟨am, bm⟩⟩

def accumulate (c : Counts) : List Counts → Counts
  | [] => c
  | x :: xs => accumulate (merge c x) xs

/-- A failure, skip, or missing required check survives any finite suffix. -/
theorem accumulate_preserves_dirty (xs : List Counts) :
    ∀ c, ¬ Clean c → ¬ Clean (accumulate c xs) := by
  induction xs with
  | nil => intro c h; exact h
  | cons x xs ih =>
    intro c h
    exact ih (merge c x) (fun hm => h ((merge_clean_iff c x).mp hm).1)

theorem no_passing_recovery (c : Counts) (xs : List Counts)
    (h : ¬ Clean c) : status (accumulate c xs) ≠ .passed := by
  intro hp
  exact accumulate_preserves_dirty xs c h ((status_passed_iff _).mp hp).2

inductive SearchRequirement where
  | graph | completed
  deriving Repr, DecidableEq

inductive SearchOutcome where
  | graphExhausted | casesCompleted | depthLimited | budgetLimited
  | interrupted | timedOut | failed | notStarted
  deriving Repr, DecidableEq

def searchSatisfied (req : SearchRequirement) (outcome : SearchOutcome) : Bool :=
  match req with
  | .graph => decide (outcome = .graphExhausted)
  | .completed => decide (outcome = .graphExhausted ∨ outcome = .casesCompleted)

structure Evidence where
  valid : Bool
  required : List Counts
  observed : List Counts
  hasRequiredFamily : Bool
  familiesSatisfied : Bool
  goalsSatisfied : Bool
  callbackError : Bool
  unclassified : Bool
  requirement : SearchRequirement
  outcome : SearchOutcome

/-- Deliberately separate from the Rust reason-string construction. -/
def strict (r : Evidence) : Bool :=
  r.valid && !r.required.isEmpty && r.hasRequiredFamily &&
  r.required.all (fun c => decide (status c = .passed)) &&
  r.observed.all (fun c => decide (Clean c)) &&
  r.familiesSatisfied && r.goalsSatisfied &&
  !r.callbackError && !r.unclassified && searchSatisfied r.requirement r.outcome

/-- Boolean implementation agrees with the explicit evidence obligations. -/
theorem strict_spec (r : Evidence) : strict r = true ↔
    r.valid = true ∧ r.required.isEmpty = false ∧ r.hasRequiredFamily = true ∧
    (∀ c ∈ r.required, 0 < c.passed ∧ Clean c) ∧
    (∀ c ∈ r.observed, Clean c) ∧
    r.familiesSatisfied = true ∧ r.goalsSatisfied = true ∧
    r.callbackError = false ∧ r.unclassified = false ∧
    searchSatisfied r.requirement r.outcome = true := by
  simp [strict, List.all_eq_true, status_passed_iff, and_assoc]

theorem strict_required_clean (r : Evidence) (h : strict r = true)
    (c : Counts) (hc : c ∈ r.required) : 0 < c.passed ∧ Clean c :=
  ((strict_spec r).mp h).2.2.2.1 c hc

theorem strict_rejects_missing_required (r : Evidence) (c : Counts)
    (hc : c ∈ r.required) (hm : 0 < c.missing) : strict r ≠ true := by
  intro h
  have hz := (strict_required_clean r h c hc).2.2.2
  omega

/-- Even an undeclared observed failure/skip must prevent a strict pass. -/
theorem strict_rejects_dirty_observation (r : Evidence) (c : Counts)
    (hc : c ∈ r.observed) (hd : ¬ Clean c) : strict r ≠ true := by
  intro h
  exact hd (((strict_spec r).mp h).2.2.2.2.1 c hc)

theorem strict_rejects_empty_catalog (r : Evidence) (h : r.required = []) :
    strict r = false := by
  simp [strict, h]

theorem strict_rejects_no_required_family (r : Evidence)
    (h : r.hasRequiredFamily = false) : strict r = false := by
  simp [strict, h]

theorem strict_rejects_invalid_report (r : Evidence) (h : r.valid = false) :
    strict r = false := by
  simp [strict, h]

theorem strict_rejects_callback_error (r : Evidence) (h : r.callbackError = true) :
    strict r = false := by
  simp [strict, h]

theorem strict_rejects_unclassified (r : Evidence) (h : r.unclassified = true) :
    strict r = false := by
  simp [strict, h]

theorem strict_graph_requires_exhaustion (r : Evidence)
    (hg : r.requirement = .graph) (h : strict r = true) :
    r.outcome = .graphExhausted := by
  rcases (strict_spec r).mp h with ⟨_, _, _, _, _, _, _, _, _, hs⟩
  simpa [searchSatisfied, hg] using hs

end Statelessness.Assurance
