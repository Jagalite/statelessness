# Python public pipe host

`host.py` supplies the SPEC model to `DebugSession` and `ProtocolSession`, then
passes binary stdin/stdout directly to public `serve`. It does not intercept
commands, author responses, normalize fields, or implement framing. Callback
entry lines are flushed immediately to stderr. The only model options are
`--read-only`, `--exact`, and `--cache-one`.

After public `serve` returns on clean EOF or raises a transport error, the host
flushes exactly one stderr line `FINAL <state> <revision> <sequence>`. Each value
comes directly from the public typed session properties; callback logs are not
used to reconstruct state. The host explicitly ignores SIGPIPE where available,
allowing an actual closed stdout reader to raise `BrokenPipeError` through
`serve`. Its final-state read therefore follows the library's disconnect cleanup
and precedes the exit status. Root's broken-output-pipe case expects nonzero exit
and `FINAL 1 1 1` after one committed turn. Clean EOF exits zero. Other transport
failures also exit nonzero and report the actual retained session state.

Implementation is source-authored and unexecuted. Root owns every build, test,
installation, and pipe comparison through shared-default Kyoto. The parent
`compare.py` owns the independent expected wire responses and raw receipts.

## Fresh isolated consumer argv

Use a new root-owned scratch directory (example below must not already exist).
These are proposed commands for the root Kyoto job; none were executed by the
Python implementation agent. Copy from root's frozen source snapshot if it has
already made one. The example uses the assigned checkout as that source.

```sh
mkdir /private/tmp/n12-python-wire-oct22-consumer
ditto /Users/jagatranvo/.t3/worktrees/stateless/n12-wire-resume-oct22/python /private/tmp/n12-python-wire-oct22-consumer/package
cp /Users/jagatranvo/.t3/worktrees/stateless/n12-wire-resume-oct22/scripts/native-parity/n12-wire/python/host.py /private/tmp/n12-python-wire-oct22-consumer/host.py
python3 -m venv /private/tmp/n12-python-wire-oct22-consumer/builder
/private/tmp/n12-python-wire-oct22-consumer/builder/bin/python -m pip install 'setuptools>=77' wheel
/private/tmp/n12-python-wire-oct22-consumer/builder/bin/python -m pip freeze --all
/private/tmp/n12-python-wire-oct22-consumer/builder/bin/python -m pip wheel --no-deps --no-build-isolation --wheel-dir /private/tmp/n12-python-wire-oct22-consumer/wheels /private/tmp/n12-python-wire-oct22-consumer/package
python3 -m venv /private/tmp/n12-python-wire-oct22-consumer/consumer
/private/tmp/n12-python-wire-oct22-consumer/consumer/bin/python -m pip install --no-deps --no-index /private/tmp/n12-python-wire-oct22-consumer/wheels/statelessness-0.1.0-py3-none-any.whl
/private/tmp/n12-python-wire-oct22-consumer/consumer/bin/python -I -B -m unittest discover -s /private/tmp/n12-python-wire-oct22-consumer/package/tests -p test_debugger_wire.py -v
```

Host argv for `compare.py` (the harness appends each scenario's options):

```json
["/private/tmp/n12-python-wire-oct22-consumer/consumer/bin/python", "-I", "-B", "/private/tmp/n12-python-wire-oct22-consumer/host.py"]
```

Set its `cwd` to `/private/tmp/n12-python-wire-oct22-consumer`. Include the copied
host, wheel, installed `statelessness` package, and interpreter in the root
artifact identity receipt. `-I` prevents source-tree/PYTHONPATH imports; `-B`
prevents runtime bytecode writes. Root should capture interpreter and builder
package versions and verify the installed module path before comparison.

The added focused suite is `python/tests/test_debugger_wire.py`. Existing
`test_debugger_protocol.py` and `test_debugger_session.py` remain relevant
regression gates. No full N12 closure, publication, or platform claim follows
from this finite fixture.
