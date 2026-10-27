# Installed causal evidence consumers

These applications execute threshold transitions, check each once, and hand sealed
results and explicit causal contexts to the public EvidenceRecorder. Their full
v2 artifacts retain exact v1 audit traces, phase boundaries, ordered provenance,
causal links, and truthful producer engine/monitor configuration.

Submit the driver through Kyoto after all engine sources are frozen:

```
python3 scripts/check-native-evidence-consumers.py --rust validation/corpus/target/debug/native-evidence --rust-portable validation/corpus/target/debug/stateless-corpus --output /private/tmp/NEW-OUTPUT
```

The driver installs local Python wheels/npm tarballs and stages Go/Swift source
dependencies outside the checkout. Every ordered pair across the five engines
runs in a fresh process for four operations: exact v2 read/rewrite and phase-aware
replay, explicit verified v1 upgrade, exclusive diagnostic export, and passive
structural v1 reading. Main artifacts and upgraded artifacts are compared with
independently specified complete normalized fields; raw v2 rewrites must preserve
all bytes. V1 parsing receives no model and must leave later phases unknown.

Producer controls exercise exact check-once counts, coordinated metadata/cause/
trace eviction, retained output-index validation before planned eviction, phase
mismatch detection, empty causal contexts after verified upgrade, whole-output
and step diagnostic caps, and no-overwrite private new-file exports. The driver
checks exact diagnostic JSON and actual Unix mode 0600. Existing four-profile
portable conformance and cross-engine replay remain a separate regression gate.

This is finite qualification on the executing host. Provenance and causal labels
are host assertions, not authentication. Limits bound serialized evidence and
structural work, not arbitrary application callback allocations or process RSS.
TypeScript filesystem behavior belongs to the explicit Node adapter; browser core
filesystem exports are not claimed. New-file exports sync the file and reject
replacement; they do not establish rename-atomic publication or directory fsync.
No terminal model-error reproduction, performance improvement, registry release,
profile promotion, or complete N05/N11 closure follows from this application.
