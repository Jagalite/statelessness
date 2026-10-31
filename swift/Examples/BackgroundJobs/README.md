# Background job consumer

Run `swift run --package-path swift/Examples/BackgroundJobs BackgroundJobs` from
the repository root. This standalone SwiftPM consumer uses the same reducer for
finite verification and an async host. The negative control accepts stale job
completions; the fixed model must exhaust its finite graph without failures or
skipped checks. The host starts real Swift tasks and routes the reducer's cancel
effect to the first task. Actor-controlled gates suspend each operation, then
deliberately allow the cancelled task to finish after a second job starts. The
reducer must ignore that late completion and accept the second job's completion.
This controlled operation simulates an external service that ignores cancellation;
it is not a production scheduler or evidence of network interoperability.
The finite model uses two identifiers and rejects reuse; a real host must supply
identifiers that cannot collide with outstanding or delayed completions.
