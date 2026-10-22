# Native TypeScript campaigns

`node.mjs` runs ordinary or guided jobs using the public
`@jagalite/statelessness/node-campaign` entry. It creates three real job workers,
uses a rendezvous result channel and awaits a serial asynchronous sink. The
application module constructs fresh models and feedback. Its explicit input
adapter restores `QueueInput` instances and private fields in the caller.
Model states and feature keys contain worker-local Symbols and never transfer.

`browser.html`/`browser.mjs` use the browser-safe public package entry and one
caller-thread worker. This example does not implement browser multiworker hosting.
The local server serves the installed package public entry through an import map.
Browser execution remains a separate, unqualified host gate.

Install a previously built packed candidate in this directory, then run:

```sh
npm install --ignore-scripts /absolute/path/to/jagalite-statelessness-0.1.0.tgz
node node.mjs ordinary
node node.mjs guided
node node.mjs guided --bug
node node.mjs guided --cancel-after-first
node node.mjs guided --feedback-error --reject-sink
node serve.mjs
```

For this N06 implementation, **submit each executable command through the shared
default Kyoto** (`python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- ...`),
using the frozen candidate rather than the mutable implementation checkout.
The focused qualification runner creates a fresh tarball installation and executes
the ordinary and guided Node examples. The coordinating root owns broader
cross-language qualification and final receipts.

The optional fault flags demonstrate distinct job/property/sink outcomes; the
example exits nonzero on errors, failures, incomplete accounting or a noncompleted
campaign. It prints reports even when a sink then rejects, illustrating that a
failed delivery can have external side effects. Failed deliveries are not retried.
Cancellation and deadlines are cooperative; they cannot interrupt a running model
callback or sink. Sorting the collected job identities is display logic, not a
generic CampaignReport merge or a durable queue checkpoint.

See [the API and evidence boundary](../../../docs/native-parity/N06-TYPESCRIPT.md).
