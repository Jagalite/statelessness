# Installed modeling applications

Each consumer uses the installed public package for bounded domains, explicit
model definitions, lazy properties, canonical U8 model binding and exact replay.
The application reducer is shared by UI event handling, a background-job loop and
an async effect host. The host translates completion of external work into input
`2`; it never awaits or executes effects inside the reducer. Input `1` requests
work, input `3` cancels, and completion outside the busy state is rejected.
This small fixture is not a production request-identity/fencing protocol (N08/N09).

Run from the repository root through the shared Kyoto queue:

```
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 scripts/check-native-modeling-consumers.py --output /tmp/NEW-modeling-attempt --compiler /absolute/path/to/typescript-5.8.3-package
```

The runner stages independent packages, executes their suites, builds a Python
wheel and npm tarball, and installs clean application consumers. Go uses an
external module with a local replacement pointing to the frozen package; this
is not evidence of a published remote module tag. Swift uses an external SwiftPM
package path. Consumer processes import no Rust ABI. The async examples exercise
one cooperative completion, not a transport or concurrent stress qualification.

The JSON result emitted by each consumer names its engine and three host paths.
Assertions are executable negative controls; none are advertised as semantic
mutants. The existing native suites provide structural inspection and the known
unsound research-projection control, separately from these application examples.

`Domain` snapshots entries using Python deepcopy or explicit Go/TS/Swift clone
hooks. Duplicate entries keep their weight; `extend` preserves a successful
prefix on failure. `product` rejects excessive cardinality before mapping.
Python/TS snapshot product membership against callback append operations; Go and
Swift arrays/slices have captured iteration bounds. Go/TS/Swift product callbacks
own copying reference values, like ordinary collection mapping. User callbacks
must terminate and honor their clone contracts; entry limits do not bound their
execution time or process allocations.

Indexed sampling consumes exactly one unbiased index. It is deliberately distinct
from `Auto`'s bounded reservoir seed contract. Python `EXHAUSTED`, TS `NO_INPUT`,
Go's boolean result and Swift's outer Optional distinguish empty domains.
Properties are declaration metadata, never assurance evidence. Empty catalogs
require explicit `unchecked`; automatic generation uses the existing `Auto`
capability and its transactional RNG/error rules. Canonical decode/encode budgets
remain enforced by the unchanged declaration binders.
