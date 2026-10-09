#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
# Identity binds both implementations, projections, codecs, and build toolchains.
identity=$(python3 - <<'PY'
import hashlib,pathlib,subprocess,os
h=hashlib.sha256()
files=[pathlib.Path(p) for p in ['Cargo.toml','Cargo.lock','build.rs','bindings/c/stateless.h','bindings/go/go.mod','examples/go-jobs/Cargo.toml','examples/go-jobs/Cargo.lock','examples/go-jobs/build.rs','scripts/check-go.sh']]
for root in ['src','bindings/go','examples/go-jobs/src']:
 files+=sorted(p for p in pathlib.Path(root).rglob('*') if p.suffix in ['.rs','.go','.c','.h'])
for p in files:h.update(str(p).encode());h.update(p.read_bytes())
for cmd in [[os.environ.get('RUSTC','rustc'),'-vV'],['go','version'],['go','env','GOOS','GOARCH','CGO_ENABLED','CC'],['cc','--version']]:h.update(subprocess.check_output(cmd))
for key in ['RUSTFLAGS','CARGO_ENCODED_RUSTFLAGS','CGO_CFLAGS','CGO_CPPFLAGS','GOFLAGS']:h.update(key.encode());h.update(os.environ.get(key,'').encode())
print('paired-sha256:'+h.hexdigest())
PY
)
# cgo does not track external native-library contents in its build cache. Bind
# this compilation to the native source identity so changed Rust relinks Go too.
export CGO_CPPFLAGS="${CGO_CPPFLAGS:+$CGO_CPPFLAGS }-DSTATELESS_CONFORMANCE_BUILD_ID=0x${identity#paired-sha256:}"
JOBS_BUILD="$identity" CARGO_INCREMENTAL=0 CARGO_TARGET_DIR="$PWD/examples/go-jobs/target" cargo build --manifest-path examples/go-jobs/Cargo.toml
export DYLD_LIBRARY_PATH="$PWD/target/debug${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
export LD_LIBRARY_PATH="$PWD/target/debug${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export CGO_LDFLAGS="-L$PWD/examples/go-jobs/target/debug -L$PWD/target/debug"
# Test the standalone SDK against its shared library. The tagged example and
# its SDK calls link only the static bridge, which contains the same engine.
CARGO_INCREMENTAL=0 CARGO_TARGET_DIR="$PWD/target" cargo build --lib
(cd bindings/go && go test -count=1 ./... && go test -count=1 -tags stateless_jobs ./... && go build -tags stateless_jobs -ldflags "-X main.buildIdentity=$identity" -o ../../target/go-jobs ./cmd/jobs)
target/go-jobs correct target/go-jobs
target/go-jobs faulty target/go-jobs
target/go-jobs replay-faulty target/go-jobs.original.trace
target/go-jobs replay-faulty target/go-jobs.reduced.trace
# Crossing implementation identities must fail exact replay.
if target/go-jobs replay-correct target/go-jobs.reduced.trace > target/go-jobs.incompatible.txt 2>&1; then
 echo 'incompatible implementation unexpectedly replayed' >&2; exit 1
fi
python3 - <<'PY'
from pathlib import Path
report=Path('target/go-jobs.incompatible.txt').read_text()
assert 'Incompatible' in report and 'paired status 3' in report, report
print('Correct/faulty identity incompatibility rejected as expected')
PY
