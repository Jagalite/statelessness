# encode-buffers-v1 (candidate)

Experimental independent-native candidate matching Rust EncodeBuffer and codec
into dispatch. It does not promote profiles or claim bounded process RSS, zero
allocation, arbitrary callback preemption, or catchable process OOM.

Construction clears logical destination length while retaining existing capacity.
The maximum bounds accepted encoded length, not an already larger allocation.
Append validates checked current_length+chunk_length before growing and rejects
the entire incoming chunk if it cannot fit. Limit/allocation failure is sticky:
all subsequent writes and finish fail even if a codec ignored an earlier error.
An empty write after failure still fails. There is no codec-visible reset or
mutable backing storage. Native byte views must be copied or held immutably so
later source mutation cannot rewrite stored evidence.

framed appends eight zero bytes, invokes the nested encoder on the same root
storage/budget, checks finish, then patches that header with the component's u64
little-endian byte length. Prefix and body share one maximum; nesting does not
create independent per-component allowances. If the prefix cannot fit, do not
invoke the callback. Callback Result error marks the shared buffer failed even
if the surrounding caller catches that error. Framing is not transactional: a
failed prefix/body may remain internally, but no successful result publishes it.
Rust does not catch panic/unwind; native contained-exception guarantees are
explicit extensions. u64 length conversion and all arithmetic must be checked.

The extend_from_vec compatibility path first validates accepted length. Rust may
move an already allocated Vec into an empty destination, including capacity larger
than the maximum; allocation occurred outside the sink. Native copies for ownership
are permitted and must be documented. Default legacy encoding remains supported
and may allocate its full result before the sink can reject it.

Prefer a present encode_state_into/input_into/output_into hook exactly once;
otherwise call the corresponding legacy encoder once and append its result.
Never run both. Every engine encoding call checks finish after callback success.
Callbacks returning errors remain engine errors; success cannot override sticky
failure. Rust marks append-limit/allocation failure and framed callback Result
errors sticky. An arbitrary unframed codec Result::Err does not by itself set
EncodeBuffer.failed; engine dispatch still rejects that encoding. Native lease
dispatchers may poison the enclosing active encoding on any callback/validation
error, including a caught nested failure. That whole-callback runtime rule is a
stronger native ownership/error extension, not a claim about Rust finish after an
arbitrary ignored unframed callback error. Route recording and replay initial state, per-turn input/output/state,
OracleCodec history, Paired reference history, and automatic/generated wrappers
through this dispatch. Non-codec BFS/fuzz/shrink must stay usable without codecs.

WithOracle checkpoint bytes are exact ASCII ORC1 followed by two u64-framed
components: actual model state then oracle history. Both encode directly into
one budget. Nested wrappers recursively use this format. No unbounded allocation
of two component blobs is an equivalent implementation. Input/output into hooks
forward to the underlying actual model; Paired history forwards to its reference
codec. Strict decode checks ORC1, each length, both codecs, and EOF with no trailing
bytes. Preserve all existing checkpoint bytes and native receiver binding.

Native callbacks receive fresh revocable leases. Revoke on every exit; an escaped
child framed view expires after its callback while an enclosing legitimate view
remains usable. Shared sticky errors propagate through active ancestors. Expired
old views must not access or poison later fresh buffers. Native invalid runtime
arguments have sticky first-error semantics even if caught/ignored. Python hooks
must return None; TypeScript hooks must return undefined and reject promises or
thenables. Framed callback return validation follows the same rule. Cleanup covers
Python BaseException and JavaScript throw undefined. These runtime guards extend
Rust's typed synchronous borrow contract; they do not make raw caller aliases,
asynchronous writes, or concurrent/reentrant access safe. Caller-owned backing
buffers require exclusive ownership during the call; opaque owners are allowed.

`conformance/encode-buffers-v1.json` pins independently constructed format math.
Buffer actions append/legacy carry lowercase hex bytes; framed actions carry
children plus optional callback_error/ignore_child_errors. Expected action results
are a tree of {ok,children}; finish_ok and encoded_hex (null on failure) prohibit
publishing partial results. initial_hex starts stale caller storage. Maximum is
an exact unsigned decimal string; wide cases require a 64-bit Rust usize target.

Dispatch vectors encode integer12 to ASCII 12 with either the real legacy default
or explicit into hook writing bytes31 then32; allocating legacy hooks error if
called when into exists. Events are legacy_state/input/output or into_ equivalents.
Automatic vectors exercise actual Auto codec forwarding. Oracle vectors encode
model12/history34; nested adds outer history56. Their mode/history_mode select
legacy/into independently. Events include legacy_history/into_history. Paired
history uses the reference model's state codec, so its second event is into_state.
Every expected byte string is hand-authored format arithmetic, never engine output.

Native lease/mutable-alias/malformed-argument/ignored-return tests remain additional
runtime qualification; Rust cannot construct the corresponding ill-typed or escaped
borrow cases. Recording/replay require the separate full audit and replay-evidence
contracts; a per-blob sink alone is not bounded replay or bounded audit recording.
