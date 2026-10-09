# Native Python Statelessness

Experimental, dependency-free **Python implementation**, not a Rust/FFI binding.
Python >=3.10. Applications retain their own state types, callbacks, and codecs.
This package implements the `core-v1`, `bfs-v1`, `rng-splitmix64-v1`, and
`trace-json-v1` profiles in `../spec/README.md`. It does not imply full Rust feature
parity: fuzzing, shrinking, guided search, oracles, runtime ring recording,
campaigns, composition helpers, and the binary `.sttrace` format are not provided.

## Install and test

From the repository root:

```sh
python -m pip install ./python
python -m unittest discover -s python/tests -v
python -m statelessness.conformance conformance/corpus.json
# The installed command is equivalent:
statelessness-conformance conformance/corpus.json
```

No Rust compiler or native library is needed. JSON is only used for fixture and
trace interchange; native application transitions are ordinary Python calls.
The corpus is a repository artifact, not implicitly downloaded by the library.
Supply its path when running the installed package outside the checkout.

## Native application example

```python
from dataclasses import dataclass
from statelessness import Check, SearchConfig, Transition, enumerate_states

@dataclass(frozen=True)
class State:
    count: int = 0

class Counter:
    def initial_state(self):
        return State()

    def inputs(self, state):
        return ["increment"]

    def step(self, state, input):
        return Transition(State(state.count + 1), outputs=("changed",))

    def check_state(self, state):
        return [Check("count.bound", "passed") if state.count <= 2
                else Check("count.bound", "failed", "count exceeded two")]

result = enumerate_states(Counter(), SearchConfig(max_states=20, max_depth=5))
assert result.termination == "failure_found"
assert result.failure.inputs == ("increment",) * 3
```

`examples/counter.py` adds application-owned codecs, JSON recording, and replay.
The optional `check_transition(before, input, transition)` callback returns
additional `Check` values. Checks and outputs are ordered. A `ModelError` is a
broken callback/adapter, not a failed property. Rejected and ignored transitions
are still checked; rejection-preserves-state is application policy, not imposed
by the framework.

`check_observed` checks a transition already executed by the application and
never calls `step`. The caller checks its initial state separately. Periodic
checking marks omitted checks as skipped rather than passing.

## State and cost contract

Callbacks must be deterministic and free of real I/O, clocks, hidden mutable
history, and effect execution. The library defensively deep-copies snapshots and
callback arguments. States must support correct `deepcopy` behavior; overriding
it to alias mutable storage violates the contract. This is not a code sandbox.

Equality must preserve future behavior, input choices, and checked properties.
Hash collisions are resolved by equality. Lists/dictionaries are supported via
linear equality lookup; hashable states are generally faster. Equal hashable
states must have equal hashes. Large states incur copying costs; no parity of
runtime, memory consumption, or throughput with Rust is claimed.

Search count limits are not process-memory limits. `TraceLimits` bound retained
encoded payloads, blob sizes, and item counts; callbacks can allocate before
returning. JSON serialization has a separate byte limit. Exact replay verifies
only the recorded prefix and identities, not graph exhaustion or a callback error
after that prefix. Build mismatch requires explicit opt-in and never bypasses
model/property/codec version checks.

## Build distributions

```sh
python -m pip install build
python -m build python
# Or with a provisioned setuptools >=77 backend and no dependency downloads:
python -m pip wheel --no-build-isolation --no-deps ./python -w dist
```

The wheel is pure Python (`py3-none-any`). Nothing here publishes to PyPI.
The source distribution is also supported; release qualification should test an
installed wheel in a fresh environment, not just imports from `src`.
