"""Additional exact-text/transport vectors shared by all native implementations.

Expectations are constructed from the portable specification, not engine output.
This extends qualification without rewriting the original 94-case corpus.
"""
from copy import deepcopy


def check(id, status="passed", details=""):
    return dict(id=id, status=status, details=details)


def identity(name, build="fixture-v1"):
    return dict(name=name, model_version=1, properties_version=1, codec_version=1, build=build)


def request(operation, model=None, **kw):
    return dict(version=1, operation=operation, **({"model": model} if model else {}), **kw)


def trace(model, inputs):
    """Only the one-edge table constructed below: no general reducer/oracle."""
    row = model["states"][0]
    edge = row["edges"][0]
    steps = [dict(input=i.encode("utf-8").hex(), disposition=edge["disposition"],
                  outputs=[x.encode("utf-8").hex() for x in edge["outputs"]],
                  post_state=row["id"].encode("utf-8").hex(), checks=edge["checks"])
             for i in inputs]
    return dict(format="stateless.trace-json", version=1, metadata=identity(model["id"]),
                initial_state=row["id"].encode("utf-8").hex(), initial_checks=[],
                steps=steps, termination="completed", error="")


def cases():
    for n, value in enumerate(["", "é", "e\u0301", "\ufeffx", "\x00", "😀", "__proto__", "constructor", "0", "4294967295", "a\\b\"c", "\u2028\u2029"]):
        # Exact same text appears in a state ID, input, output, reason and check ID.
        model = dict(id=f"text-{n}", initial=value, states=[dict(id=value, edges=[dict(
            input=value, to=value, outputs=[value], disposition=dict(kind="ignored", reason=value),
            checks=[check(value)])])])
        yield dict(id=f"portable-text-record-{n}", request=request("record", model, inputs=[value], max_steps=1), expected=dict(trace=trace(model, [value])))
        yield dict(id=f"portable-text-replay-{n}", request=request("replay", model, trace=trace(model, [value]), allow_build_mismatch=False),
                   expected=dict(outcome="exact", steps_verified=1, failure_reproduced=False, build_matches=True, step=None, field=None))
    # Canonically equivalent Unicode spellings MUST remain distinct states.
    model = dict(id="scalar-identity", initial="é", states=[dict(id="é", edges=[dict(input="go", to="e\u0301")]), dict(id="e\u0301")])
    yield dict(id="portable-distinct-unicode-states", request=request("enumerate", model, config=dict(max_states=2,max_transitions=1,max_depth=2)),
               expected=dict(termination="graph_exhausted",states=2,transitions=1,max_depth_reached=1,skipped_checks=0,failure=None))
    # Distinct input spellings must not be rejected as conflicting duplicates.
    model = dict(id="input-identity", initial="s", states=[dict(id="s",edges=[dict(input="é",to="a"),dict(input="e\u0301",to="b")]),dict(id="a"),dict(id="b")])
    yield dict(id="portable-distinct-unicode-inputs",request=request("enumerate",model,config=dict(max_states=3,max_transitions=2,max_depth=2)),
               expected=dict(termination="graph_exhausted",states=3,transitions=2,max_depth_reached=1,skipped_checks=0,failure=None))
    # Exact identity comparison is independent from Swift's canonical String ==.
    for field in ("name", "build"):
        model = dict(id="identity", initial="s",metadata=identity("identity"),states=[dict(id="s")])
        model["metadata"][field] = "é"
        saved = dict(format="stateless.trace-json",version=1,metadata=deepcopy(model["metadata"]),initial_state="73",initial_checks=[],steps=[],termination="completed",error="")
        saved["metadata"][field] = "e\u0301"
        for allow in (False, True):
            exact = allow and field == "build"
            yield dict(id=f"portable-identity-{field}-{allow}",request=request("replay",model,trace=saved,allow_build_mismatch=allow),
                       expected=dict(outcome="exact" if exact else "incompatible",steps_verified=0,failure_reproduced=False,build_matches=field!="build",step=None,field=None))
    for changed in ("id", "details"):
        observed = check("é", "failed", "é")
        expected = deepcopy(observed)
        expected[changed] = "e\u0301"
        model = dict(id="checks",initial="s",states=[dict(id="s",checks=[observed])])
        saved = dict(format="stateless.trace-json",version=1,metadata=identity("checks"),initial_state="73",initial_checks=[expected],steps=[],termination="property_failed",error="")
        yield dict(id=f"portable-unicode-check-{changed}",request=request("replay",model,trace=saved,allow_build_mismatch=False),
                   expected=dict(outcome="diverged",steps_verified=0,failure_reproduced=changed=="details",build_matches=True,step=None,field="initial checks"))
    # These two reason strings are semantically distinct despite rendering alike.
    model = dict(id="reason",initial="s",states=[dict(id="s",edges=[dict(input="x",to="s",disposition=dict(kind="rejected",reason="é"),outputs=[],checks=[])])])
    saved = trace(model,["x"]); saved["steps"][0]["disposition"] = dict(kind="rejected",reason="e\u0301")
    yield dict(id="portable-unicode-rejection",request=request("replay",model,trace=saved,allow_build_mismatch=False),
               expected=dict(outcome="diverged",steps_verified=0,failure_reproduced=False,build_matches=True,step=1,field="disposition"))
    # Independent arithmetic vectors from the normative formula; no engine import.
    for seed in [0,1,42,2**63,2**64-1]:
        bounds = [0,1,2,3,2**32-1,2**63+1,2**64-1]*7
        state = seed
        def next_value():
            nonlocal state
            state = (state + 0x9e3779b97f4a7c15) % 2**64
            z = state
            z = ((z ^ (z >> 30)) * 0xbf58476d1ce4e5b9) % 2**64
            z = ((z ^ (z >> 27)) * 0x94d049bb133111eb) % 2**64
            return z ^ (z >> 31)
        raw = [str(next_value()) for _ in range(100)]
        indices = []
        for upper in bounds:
            if upper == 0:
                indices.append(None)
            else:
                threshold = 2**64 % upper
                value = next_value()
                while value < threshold:
                    value = next_value()
                indices.append(str(value % upper))
        yield dict(id=f"portable-rng-{seed}",request=request("rng",seed=str(seed),draws=100,bounds=[str(x) for x in bounds]),
                   expected=dict(raw=raw,indices=indices,next=str(next_value())))


def transport():
    for raw in [b'\xef\xbb\xbf{"version":1,"operation":"hello"}',
                b'{"version":1,"ver\\u0073ion":1,"operation":"hello"}',
                b'{"version":1e0,"operation":"hello"}',
                b'{"version":1,"operation":"hello","x":"\\udc00"}',
                b'{"version":1,"operation":"hello","x":"\\ud800\\ud800"}',
                b'{"version":1,"operation":"hello","x":"\xc0\xaf"}',
                b'{"version":1,"operation":"hello","x":"\xed\xa0\x80"}',
                b'{"version":1,"operation":"hello","x":"\xf4\x90\x80\x80"}']:
        yield raw, {"error":"invalid_request"}
    # Exactly 4 MiB, INCLUDING newline, is valid; one extra byte is rejected.
    valid = b'{"version":1,"operation":"enumerate","model":{"id":"empty","initial":"s","states":[{"id":"s"}]},"config":{"max_states":1,"max_transitions":0,"max_depth":0}}'
    padded = valid + b' ' * (4*1024*1024 - 1 - len(valid))
    yield padded, dict(termination="graph_exhausted",states=1,transitions=0,max_depth_reached=0,skipped_checks=0,failure=None)
    yield padded+b' ', dict(error="invalid_request")
