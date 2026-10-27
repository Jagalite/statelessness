# search-v1 (candidate)

Status: experimental candidate for N03/N04. This document does not promote a
profile or establish implementation qualification. It specifies independent
native generation, deterministic sequence fuzzing, and causal shrinking against
`src/model.rs`, `src/automatic.rs`, and `src/explore.rs`. Guided fuzzing is a
separate gap. Passing this finite corpus is necessary but not sufficient.

## Generation and randomness

RNG is SplitMix64: unsigned 64-bit state initialized to the exact seed. On each
word add 0x9e3779b97f4a7c15 modulo 2^64, then apply
`z=(z xor (z>>30))*0xbf58476d1ce4e5b9`,
`z=(z xor (z>>27))*0x94d049bb133111eb` (both modulo 2^64), then
`z xor (z>>31)`. Index(0) yields no index without consuming a word.
Index(n>0) rejects words below `(2^64-n)%n`, returning word%n otherwise.
Seeds and RNG words must retain all 64 bits, including in JavaScript.

Generate is state-aware and returns an explicit absent/present result. Absence
ends a case; a present value must satisfy is_enabled before step. Custom
is_enabled defaults to true; simpler_inputs defaults to empty. A disabled
generated input is an error, never a skipped transition. A host-language null
sentinel must not silently exclude a supported input value: offer an explicit
presence wrapper or document and enforce the narrower input domain.

Auto wraps a finite enumerator in its declared stable order. It samples entries
(including duplicates) by reservoir sampling, retaining the first without RNG
consumption; for entry number k>1 replace iff index(k)==0. Work uses a cloned RNG
and commits only after a successful complete scan. Empty/singleton domains
consume no words; oversized domains and callback errors roll RNG back. Positive
max_candidates defaults to 100000. Pull at most cap+1 entries: the extra pull
proves overflow and is never sampled. Do not choose from a truncated prefix.
Membership scans in order and returns at the first match inside the cap; it need
not validate the remaining suffix. A miss at exactly the cap requires one more
pull to distinguish exhaustion from overflow. Zero cap is an error. Comparisons
and iterator errors remain errors. Enumeration may be lazy; limits cannot
preempt one callback/iterator pull.

## Fuzzing

Defaults: seed=0, cases=1000, max_steps=100, max_transitions=100000,
mutation_percent=50 (integer 0..100). Other budgets may be zero. One RNG spans
all cases. Each started case creates initial state and increments cases, then
checks initial properties even when max_steps or max_transitions is zero.
Initial failures have empty witnesses and initial_state phase. Zero cases invokes
no model callbacks. Count executed steps globally; check transition budget at
each requested step before generation, even if the domain would be empty. Thus
zero steps completes cases, while zero transitions with positive steps starts
one case then returns transition_limit. Exact budget consumed on the final step
of the final case returns cases_completed unless that step fails.

Keep only the previous completed passing case's executed inputs. When nonempty,
consume index(100) and mutate iff it is below mutation_percent (the probability
word is consumed even at 0 or 100). A nonmutating case starts from an empty
candidate. Mutation copies previous, draws start=index(length), draws
span=1+index(length-start), deletes that span, then draws replace_at=index(new
length+1). At step i, reuse candidate[i] only if i!=replace_at and is_enabled
succeeds. Otherwise generate against the current state. Replacement replaces
rather than inserts; it does not shift the remaining candidate positions.
A reused input does not require a second enabled check. A disabled reused input
falls back to generation; a disabled generated input is an error.

After step, increment transitions, append input, check successor state then
transition properties, collecting all failures in that order. Stop at the first
failing transition, preserving all its failed checks and the executed prefix.
Skipped checks increment skipped_checks; they are never failures. Passing
reports include termination, original seed, started cases, transitions,
skipped_checks, and absent failure. No-failure search completion is not proof.

## Shrinking

Target is the original failure's first failed check's stable ID **and phase**
(initial_state, state, transition); descriptions are not identity. max_attempts
must be positive (default 10000). Replay original first and count it as attempt
1. Each replay starts fresh, checks initial properties, checks enabled before
each step, then successor and transition checks. Stop at first failed transition
and return its prefix. Validate target reproduction; invalid causality, no
failure, or another target during original validation is an error. A resource
stop during validation preserves supplied evidence with validated_original=false.
Replay transitions count globally, including original validation. Optional
max_replayed_transitions may be zero. Budget is checked before enabled; disabled
inputs consume no transitions. Failure from a completed check wins over a
simultaneous cancellation request.

Visited sequences start with the validated failure prefix. Compare inputs using
model identity, not object identity or hashes. Skip a candidate equal to an
accepted sequence without replay or attempt. Only accepted sequences enter this
set. A new candidate that replays to an already accepted failing prefix is
already_seen, consumes an attempt, and is not retained.

Deletion: groups=2; chunk=ceil(length/groups); consider contiguous ranges
[start,min(start+chunk,length)) in ascending start order. On retention restart
with groups=max(2,groups-1). On a failed sweep, stop deletion if groups>=length,
otherwise groups=min(length,2*groups) and repeat. Then visit positions in order
and simpler_inputs alternatives in declared order. Retain the first new replay
that contains target. Restart deletion after retention. Finite cyclic alternatives are
bounded by visited accepted sequences. This does not bound arbitrary iterator
work: an infinite simplification stream that repeats an already accepted input
can keep producing skipped candidates without spending attempts. max_attempts
bounds replay attempts, not total iterator pulls, equality comparisons, callback
time, or allocations. Use cooperative cancellation/deadline to stop such a
stream between pulls/comparisons; neither can preempt one nonreturning callback.
This is also a limitation of the Rust algorithm. Stop search_complete when no
alternative is retained; this is not a global minimality guarantee.

Before an unseen replay, if attempts==max_attempts stop attempt_limit without a
new history entry. Replay outcomes: retained, invalid_causality, no_failure,
different_failure, already_seen, resource_limit. History entries contain
1-based attempt, full candidate_length (not failing prefix length), operation
(validate_original/remove with exclusive end/simplify with zero-based index and
alternative), and outcome. Reports preserve original, minimized, attempts,
history, replayed_transitions, validated_original, and termination.

## Search observations

The observed fuzz entry point runs the same generator, mutation, checks, and
budgets as ordinary fuzzing. Observation is optional and off by default. It does
not grant a hook authority to change state/input identity, transition contents,
property results, RNG consumption, or which candidates execute. A report from a
nonthrowing observer with no external control side effects must equal the
unobserved report. Observers may record evidence and request cancellation through
the ordinary external control handle; that affects only the next control poll.

* `initial(state, checks)` fires once per successfully initialized and checked
  case, including an initial property failure. It receives the complete ordered
  initial-state check batch (passed, failed, and skipped). It fires before those
  checks are reduced to failure/skipped counts. No initial hook fires if
  initial_state or its checker errors.
* `transition(before, input, after, state_checks, transition_checks, history)`
  fires after step and both checker callbacks succeed. `after` includes the full
  transition (successor, output, disposition/reason); checks are separate complete
  ordered batches including passed/failed/skipped results. It fires for a
  property-failing turn as well as a passing one, before report failure
  classification. `history` is the executed prefix from that case's initial
  state, including the current input; it is not a corpus sequence, mutation
  proposal, traversal order, or a cross-case history. Rust fuzz passes this prefix
  even when the observer's optional `needs_history()` returns false. That flag
  defaults false and permits other search algorithms to avoid materializing
  history; it must not silently truncate fuzz evidence.
* `check_error(phase, checks, complete)` reports checker emissions on an error,
  not a successfully covered initial state or edge. Initial checker error:
  one initial_state batch, complete=false. Successor-state checker error: one
  state batch, complete=false, with no transition check callback/hook. Transition
  checker error: first the completed state batch with complete=true, then the
  partial transition batch with complete=false. Empty emitted batches are still
  reported. There is no normal initial/transition hook for these error paths.
  No check_error hook is invented for errors from initial_state, generate,
  is_enabled, step, or input equality. The original operation remains an error,
  even if its partial batch already contains a failed check.

Languages with eager check-list return APIs cannot observe checks accumulated
inside a checker that throws before returning its list; their partial batch is
empty. To claim parity for partial-emission evidence, expose an incremental
check sink or another explicit means to preserve successfully emitted checks.
Never claim a partial batch was complete or infer unobserved checks.

Rust SearchObserver callbacks return unit rather than Result. Rust does not
convert observer panics into ModelError or a search report; panic recovery is
outside this contract. Native languages may expose throwing/fallible observers:
a normal hook error must abort with observer context, never fabricate a property
violation or successful report. While reporting a checker failure through
check_error, retain the original checker error as primary; a secondary observer
error may be chained/attached using the host language's error facilities. This
native recoverable-error policy is an explicit API extension, not evidence that
Rust's infallible hooks offer equivalent exception handling.

Observer inputs must be immutable/read-only for the duration of the call or
independent snapshots. A native implementation accepting mutable application
objects must detach observer values (including nested state, input, output,
checks, and history), or explicitly reject values for which its isolation
mechanism cannot supply that guarantee. Do not expose engine-owned mutable
containers. An observer mutating its received copies must not alter live model
state, previously accepted candidates, property outcomes, or returned failures.
Rust uses shared borrowed references rather than snapshotting; interior-mutability
or captured model side effects can still violate the observation-only contract.
The same restriction on captured application/model side effects applies to
native observers: detached arguments cannot sandbox arbitrary external effects.

Shrinking currently replays with an unobserved observer in Rust; this profile
does not promise shrink observation hooks. Qualification must separately cover
passing/failing hooks, prefix histories, complete/partial error batches,
observer errors, and mutable observer arguments. The finite search TSV remains
the algorithm corpus and does not alone qualify these observation guarantees.

## Controls and errors

Cancellation is cooperative and checked before work and between callbacks,
simplification iterator pulls, identity comparisons, and replay steps; it cannot
interrupt user callbacks. A complete Auto domain scan is one cooperative
generation/membership callback: no control poll occurs between its entry pulls.
Its candidate cap bounds pulled entries, not elapsed callback time.
Cancellation has priority over an expired deadline at the same poll. Zero
duration means deadline at entry. Callback errors propagate with operation
context and beat simultaneous cancellation; no partial success is manufactured.
Checks from a completed transition are evaluated before a following control
poll; violations therefore win at that boundary. Fuzz transition-budget polling
precedes its per-step control poll; shrink polls control before transition
budget. No clock is accessed absent a deadline. Rust unknown-OS Wasm rejects requested
wall-clock limits explicitly; this target-specific restriction does not forbid
independent native JavaScript/browser implementations from using their clocks. Finite fixture controls below are `none`,
`cancelled` (set at entry), or `deadline` (zero duration); timing races need
separate deterministic callback-triggered tests.

## Hand-authored finite corpus

`conformance/search-v1.tsv` uses decimal unsigned seeds/budgets, `-` for absent
fields, JSON integer arrays for original/witness and nested arrays for executed
passing/failing case traces. `transitions` means replayed_transitions for shrink.
`error` is a stable corpus category, not required verbatim error text. Empty
failed_ids/phases are `-`; a failure uses the indicated singleton ID/phase.
Adapters must compare reports and errors, and capture executed cases when trace
is present. Fixture properties below otherwise all pass, outputs are irrelevant,
initial state=0, check lists contain no skips. All inputs use integer equality.

* singleton: finite domain [1] in every state; step adds input; no failure.
* empty: domain []; no failure.
* binary: finite domain [1,2]; step adds input; no failure. Auto sampling.
* digits: custom generation draws next_u64()%10 (not Auto); every integer 0..9
  is enabled in every state, step adds input, no failures or simplification.
* initial: singleton domain, initial check ID `bad` fails (and subsequently).
* threshold: singleton domain, state check `bad` fails iff state>=3; no simpler
  hints. Original [1,1,1] fails on its third step.
* causal: domain [1] at state 0, [2] at state 1, [] elsewhere; step sets state to
  input; state check `bad` fails at state 2. Membership must obey domain.
* phase: domain [1,2] at every state; step sets state to input. State check
  `same` fails at state 1; transition check `same` fails at successor 2.
  simpler_inputs(1)=[2], all others empty.
* cycle: domain [1,2]; step sets state to input; state check `bad` fails at any
  nonzero state. simpler_inputs(2)=[1], simpler_inputs(1)=[2].

Goldens are derived from these finite definitions, not engine recordings.
Threshold deletion attempts have lengths 3,1,2,2,2,2 and execute 3+1+2+2+2+2=12
steps; no failed candidate is cached. Causal deletion first tries disabled [2]
then passing [1]. Phase simplification reproduces the ID in the wrong phase and
must be rejected. Cycle accepts [1] then skips [2] by visited identity. Seed 0's
first words are 0xe220a8397b1dcdaf (odd) and 0x6e789e6aa1b965f4 (even), so the
first two binary reservoir samples are 1 then 2. Mutation's singleton traces are
behavioral smoke cases; separate RNG/mutation callback traces must qualify exact
random consumption and disabled reuse fallbacks before promotion.

`conformance/search-v1-history.json` pins every shrink attempt operation and
outcome. Entries use zero-based indices and exclusive removal ends. Rows with
errors have no report/history. All other shrink rows must match exactly.

The digits mutation vector pins replacement rather than insertion and RNG
consumption even for index(1): seed0, three cases of three steps, mutation100
yields [[5,0,9],[5,9,0],[5,3,1]]. First case consumes words1..3. Case2 uses word4
for probability (44), word5 for start (1), word6 for span (1), word7 for
replacement (2), then word8 generates 0. Case3 uses word9 for probability (99),
word10 for start (2), word11 for span (1, consuming index(1)), word12 for
replacement (1), then words13..14 generate 3 and 1. Only six generation calls
occur; the other inputs are enabled reuse.
