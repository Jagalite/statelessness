# Installed native search consumers

These four small applications use public native generation, fuzzing, shrinking,
recording, and replay APIs. They do not call conformance runners, private engine
helpers, ABI libraries, or Rust. This is candidate `search-v1` qualification;
passing does not promote the profile, prove full N03/N04 parity, or measure speed.

Each application accepts `record PATH` or `replay PATH`. Recording fuzzes the
application, saves its original failed witness as `PATH.original.json`, invokes
causal shrinking, and saves the reduced ordinary `trace-json-v1` witness as
`PATH`. Replay loads either file in a separate process and requires an exact
reproduction of the failed property. Generation includes no-ops, so the original
failing witness contains more than three inputs. Shrinking must strictly shorten
it to three increments and preserve the state-phase `threshold.bound` violation.
Producer receipts record original/minimized lengths, successful original
validation, and a positive shrink-attempt count; the driver enforces these gates.

All models start at integer zero and generate integer `1 - rng.index(2)`.
Input `0` is a modeled no-op with no output; input `1` increments state and emits
`changed`. Both inputs are accepted and `threshold.bound` is checked after every
transition. At state three or greater
that check fails with `at least three`. State and input codecs are decimal ASCII;
outputs are ASCII. Input decoding accepts only ASCII `0` and `1`. Metadata is
`native-search-threshold`, versions 2/1/1, build `application-v2`. Fuzz seed is 0,
cases 4, max steps 32, max transitions 128, mutation 0 percent.

Run the driver through Kyoto from the repository root:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 scripts/check-native-search-consumers.py --output /tmp/native-search-consumers-unique-run
```

The output directory must be new or empty. The driver stages sources in a new
temporary directory outside the checkout and retains it for inspection. It
builds and installs a Python wheel into a new isolated venv; builds and installs
an npm tarball and compiles the consumer against its public declarations; builds
a separate Go module with an explicit replacement to staged package source and
cgo disabled; and builds a separate SwiftPM executable against staged source.
The Python host needs pip and venv. The driver provisions setuptools and wheel
in a separate builder venv and records their installed versions; that setup may
download build tooling. Runtime package installation uses only the locally built
wheel and tarball, and the TypeScript compiler must already be available.

Every producer runs once and every ordered producer/consumer pair replays both
the original and minimized artifacts in separate fresh processes, yielding 32
artifact replays across 16 ordered language pairs including same-language pairs.
Logs, package hashes, trace hashes, command receipts, source fingerprints,
consumer fingerprints, and toolchain versions are retained in the output.
`--without-swift` produces explicitly narrower 18-replay/nine-pair evidence only.
