N14 Go final candidate is frozen at scoped SHA256 `3429dc1d4ad27d2e4398146fd86ba2481c2e014b44222dbf0814ec38d710f69f` (67 Go files). Source files and all captured fixture/spec/reference inputs match the pre-submission manifest after both terminal executions.

Kyoto `c0146e5372674a78adc1bd1e2a92673b` executed full uncached `go test -count=1 ./...`; `2bce3a0dfc2c48e6b17ae060083ad440` executed `go vet ./...`. Both succeeded exit 0; requested and effective commands match, with CGO_ENABLED=0 and the absolute STATELESS_RESEARCH_CORPUS path.

The adapter executed all 67 shared public cases, comparing full actual reports, callback logs and qualification parameters. Five additional native failure cases executed; five rows are explicit Go type or language exclusions, not runtime admission passes. Focused tests cover exact ordering, error precedence, edge counters, shortest phase witnesses, unreachable materialization, mutable callback ownership, repeated buffers, preferred append-check leases, nonreflexive keys and finite traversal guards. No production pruning or promoted profile changes are inferred.

Both earlier compile failures are preserved under their original exact snapshot. The only source correction was four explicit Accept[int,int] test type arguments; core/fixtures did not change. All original logs are preserved losslessly as gzip, with SHA256 and sizes in log-ledger.json.
