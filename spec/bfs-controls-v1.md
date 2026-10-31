# bfs-controls-v1 (candidate)

Experimental N01 candidate extending independent native BFS, based on the complete
public Rust `enumerate_observed` kernel in `src/explore.rs`. This contract does
not close append-only check storage, rich/bounded replay, or all of N01. Static
expectations precede execution of the real public Rust reference tests.

## Controls, callbacks, and report boundaries

max_states is positive (default 100000); zero is a configuration error **before**
pre-start control checking. Defaults max_transitions=1000000, max_depth=100.
RunLimits are cooperative: cancellation wins over an expired deadline at the
same poll, zero duration expires at entry, and callbacks cannot be interrupted.
Capture budget values and the cancellation callback/reference at entry, before
model callbacks. Later changes to caller-owned option containers cannot raise or
lower those budgets; the captured cancellation source's state remains live.
Pre-start cancellation/deadline invokes no callbacks and reports states=0,
transitions=0, max_depth_reached=0, skipped_checks=0, unknown retained estimate.

After initial_state succeeds, report states=1 and finish its state checker and
initial observer without an intervening control poll. Initial failure wins over
cancellation requested during that boundary and returns before estimation/hash.
Checker errors remain errors even if an earlier check failed or cancellation was
requested. Skipped checks are counted across every executed check batch,
including duplicate/failing edges; the u64 skipped_checks counter saturates at
2^64-1 rather than wrapping, rounding, or erroring. Poll after completed successful
initial checks, then call initial estimated_state_bytes even without a byte cap. Store its returned estimate in
the report **before** the post-estimate poll. Cancellation then wins over missing
estimates or an exceeded cap. If the initial estimate exceeds the cap, report
estimated_state_byte_limit, states=0, known retained bytes=0. A missing capped
estimate is an error. Hash the initial state only after byte admission succeeds,
then poll control before retaining hash/frontier structures.

FIFO nodes store admitted state, predecessor/input, and depth. At each dequeued
node poll, create its declared input iterator, then poll. At max_depth advance
exactly once and poll: an entry sets a remembered depth cutoff, exhaustion does
not; execute no edge and apply no transition cap on this boundary path. Otherwise
poll before each advance and after it. This post-next poll precedes both checking
exhaustion and the transition budget. At the transition cap one extra next probe
is still required to distinguish an exhausted domain from an unexamined edge.
Exhaustion at the exact final edge budget returns graph_exhausted. A remembered
depth cutoff becomes depth_bound only after the queue exhausts normally; other
terminations and errors win immediately.

Once step starts, finish step, successor-state checker, transition checker, and
transition observer without control polls between them. Increment transitions
and max_depth_reached immediately after successful step, before checks. Count
all executed edges including duplicates and rejected admissions. Observe/collect
all successor-state checks first, then all transition checks; collect every
failure from that turn. A completed property failure wins over cancellation,
state/byte caps, and duplicate identity. Its witness is the predecessor prefix
plus current input. A checker error wins over a collected failure/cancellation.

After a passing checked transition poll, hash successor, poll, then resolve its
full-hash collision chain in newest-admitted-first order. Poll before and after
each actual equality comparison; hash collisions never establish identity.
Duplicates are observed and counted but never estimated/admitted/charged twice.
A new state hitting max_states returns state_limit **before** its estimate
callback or byte cap. Otherwise estimate it and poll before validating/summing
or admitting. Cancelled successor estimation leaves report totals for previously
admitted states unchanged. On admission enqueue FIFO, set states to admitted
count and update retained bytes. max_depth_reached counts executed edges, even
when their successor fails a property or an admission cap.

## Estimates and integer domains

Estimates describe retained **model state representation**, not temporary
successors, predecessor inputs, maps/frontiers, callback allocations, or process
RSS. Optional estimates are exact unsigned 64-bit values in native APIs. Rust uses
usize: shared vectors requiring values near 2^64 require a 64-bit reference target;
32-bit reference qualification must explicitly skip unsupported vectors instead
of truncating them. Metadata/counters must never pass through rounded Number.

A report starts unknown; after initial estimation it has that value or remains
unknown. For each admitted successor use checked addition. Without a cap, any
missing estimate or sum overflow makes the total unknown permanently; still
estimate later novel states. With a cap, a missing estimate is an engine error;
overflow or sum above the cap returns estimated_state_byte_limit and preserves
prior admitted totals. Equality fits. The initial state's zero estimate fits
cap zero. The estimate hook may error; an error is never converted to a cutoff.
Do not report uncapped unknown totals as zero, capped truncated prefixes as
admitted states, or platform object-size values as a cross-language RSS bound.

WithOracle combines model estimate, oracle estimated_state_bytes(history), and
wrapper overhead with checked unsigned 64-bit addition; any unknown or overflow
returns unknown. Each application estimate includes its member's owned
representation. Go and Swift expose their actual inline wrapper padding: native
pair size minus sum of member sizes, with checked/saturating validation of layout
arithmetic. This is architecture/type-dependent padding, excluding heap/RSS.
Python and TypeScript require an explicitly caller-supplied wrapper-overhead
estimate; the default is unknown, never invented zero or 16. Explicit zero is a caller
assertion and nonzero values must be included. No universal equal-byte claim is
made across these conventions. Test explicit zero/nonzero/unknown/overflow and
wrapper generation/enumeration forwarding. Paired oracle estimation delegates
to its reference model; outer WithOracle still adds its own wrapper overhead.

## Observations and error evidence

Reuse the search observer contract from `search-v1.md`: initial observation after
a completed initial checker; transition observation after both complete checker
batches including failing/duplicate/unadmitted edges. BFS alone calls
needs_history per executed edge: false produces empty history; true produces
actual root-to-parent prefix plus current input. This flag does not change fuzz's
always-present executed prefix. Observers receive detached/read-only values under
the existing native ownership contract. They cannot alter exploration/RNG/check
results by mutating their received snapshots. External cancellation remains
permitted at ordinary subsequent polls.

Checker errors invoke check_error with initial_state/state partial batch,
complete=false. A transition-check error first delivers completed state batch,
complete=true, then partial transition batch, complete=false. Never emit a
normal observation on these error paths. Partial means successfully emitted
checks, not checks never returned: Go ([]Check,error) can explicitly return a
prefix; lazy iterators/sinks can emit one before error; a throwing eager array
callback can expose no such prefix. Preserve primary checker error if diagnostic
observation fails. Rust observer callbacks are infallible/unit-returning and panics are
outside ModelError; native recoverable observer errors are an explicit extension.

## Shared finite vectors

`conformance/bfs-controls-v1.json` describes small integer models and exact report
fields/callback logs. All states deliberately hash to the same hash so equality
comparison order is explicit and collisions are exercised. Model equality is
integer equality. `chain` has 0→1→2 via input 1; state 2 has no inputs. `empty` has
no inputs. `loop` has two input 0 entries at state 0, both self edges. `diamond`
has state 0 inputs [1,2,1], states 1/2 input 3, and state 3 no inputs; step uses the
input as successor. Other model checks pass ID `ok`; optional skipped checks append
ID `skip`, reason `not run`. Initial/successor failures append ID `bad`, reason `broken`.
Transition checker emits no checks. Estimate array entries correspond to state
integer, each unsigned decimal string or null.

Log events: initial; state:N; observe_initial:N; estimate:N; hash:N; inputs:N;
next:N:INPUT (or end); step:BEFORE:INPUT; transition:BEFORE:INPUT:AFTER;
observe:BEFORE:INPUT:AFTER:HISTORY (comma-separated inputs, '-' when empty);
equal:ADMITTED:CANDIDATE; check_error:PHASE:COMPLETE:COUNT. `needs_history`
itself is not logged. A callback-triggered cancel is set after logging its
matching event, before callback returns. `cancel_at` names the first exact
matching event, or null; controls at entry are none/cancelled/deadline/both.
`error_at` targets checker event and returns an error without emitted checks.
Additional API-specific tests must preserve any check prefix emitted before an
error; no prefix is fabricated for eager throwing callbacks.

Expected logs/counters are independently hand-derived from these finite graphs
and explicit boundary ordering; they are not recordings of an engine. A Rust
public-API reference test consumes the same fixed corpus. Qualification also
requires native consumers, observer mutation/error tests, wrapper estimate
tests, callback-triggered real deadline checks, and 32-bit limits where supported.
The finite callback-triggered vectors use cancellation for deterministic exact
ordering; zero-duration entry cases pin deadline interpretation and priority.
