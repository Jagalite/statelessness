# Installed finite research consumers

These independent application adapters call the public opt-in research APIs on
the declared tables in `conformance/research-equivalence-v1.json`. Each consumer
supports `run CORPUS OUTPUT` and writes the complete normalized report, ordered
callback log and qualification parameters for each finite case. The installed
driver compares those results against independently specified fixture goldens
and the actual Rust reference. It does not enable production pruning or infer
equivalence beyond the declared finite fixture and its explicit assumptions.

`scripts/check-native-research-consumers.py` builds an isolated Python wheel,
separate Go module, npm tarball with external strict declaration compilation,
and external SwiftPM application. Submit the entire driver through Kyoto only
after the root agent declares the fixture and all implementations frozen. The
driver preserves failed outputs, bounds every command, and rejects source drift.
Native ownership, exception, cancellation or timing controls are host extensions
and must remain separate from cross-engine finite fixture results.
