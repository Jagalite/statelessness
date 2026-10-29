# Native campaign applications

These applications exercise ordinary and guided jobs through the independent
native packages, including real worker factories and serial caller-owned sinks.
They do not load the Rust engine. Rust public-reference comparisons live in
`scripts/native-campaign-consumers` and the N06 qualification harness.

Each language directory has its own application and packaging requirements:

- [Go](go): goroutines, application clone hooks and a separately installed module.
- [Python](python): spawned processes, importable factories and explicit report
  input transfer. Keep process startup under `if __name__ == "__main__"`.
- [TypeScript](typescript): a Node worker module plus application input encoding
  and decoding. The browser-safe caller API is distinct from the Node host.
- [Swift](swift): SwiftPM, worker-local models and Sendable report inputs.

Use the [language API notes](../../docs/native-parity/N06-INTEGRATION.md) and
[qualification procedure](../../docs/native-parity/N06-QUALIFICATION.md) for exact
entry points, source/package identities and recorded execution status. Run all
builds and executions through shared-default Kyoto. A committed example or queued
job is not an installed-consumer pass.

Job IDs and derived seeds remain attached to every collected result. Delivery
order is host-dependent; comparison by ID is not a CampaignReport merge or a
durable queue. N05 final qualification, N10 assurance and platform/profile
promotion remain separate.
