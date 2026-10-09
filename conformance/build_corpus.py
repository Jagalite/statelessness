"""Author-reviewed golden cases. Does not import or execute either engine.

Regenerate with python conformance/build_corpus.py. Expected counts, witnesses,
observations, and failure classifications below are specification examples, not
snapshots blindly blessed from an implementation. Review expectation changes.
"""
from copy import deepcopy as cp
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CASES = []


def check(id, status="passed", details=""):
    return dict(id=id, status=status, details=details)


def edge(input, to, **kw):
    return dict(input=input, to=to, **kw)


def model(id, rows, **kw):
    return dict(id=id, initial="s", states=[dict(id=name, **row) for name, row in rows.items()], **kw)


def config(s=10, t=100, d=10):
    return dict(max_states=s, max_transitions=t, max_depth=d)


def request(op, m=None, **kw):
    return dict(version=1, operation=op, **({"model": cp(m)} if m is not None else {}), **kw)


def add(id, rules, req, expected):
    CASES.append(dict(id=id, rules=rules.split(), request=cp(req), expected=cp(expected)))


def report(termination="graph_exhausted", s=1, t=0, d=0, skipped=0, failure=None):
    return dict(termination=termination, states=s, transitions=t,
                max_depth_reached=d, skipped_checks=skipped, failure=failure)


def failure(inputs, *violations):
    return dict(inputs=inputs, violations=[dict(phase=p, check=c) for p, c in violations])


def bfs(id, m, expected, cfg=None, rules="BFS-001 BFS-002"):
    add(id, rules, request("enumerate", m, config=cfg or config()), expected)


EMPTY = model("empty", {"s": {}})
LOOP = model("loop", {"s": {"edges": [edge("tick", "s")]}})
CHAIN = model("chain", {"s": {"edges": [edge("go", "a")]}, "a": {}})
BAD = check("edge.rule", "failed", "deliberate failure")
BADSTATE = check("state.rule", "failed", "deliberate failure")
SKIP = check("audit", "skipped", "not observed")

bfs("empty-at-zero-work", EMPTY, report(), config(1, 0, 0))
bfs("initial-failure-before-inputs", model("initial-fail", {"s": {"checks": [BADSTATE], "inputs_error": True}}),
    report("failure_found", failure=failure([], ("initial_state", BADSTATE))), config(1, 0, 0), "EXEC-001 BFS-003")
bfs("initial-skipped-is-counted", model("skip", {"s": {"checks": [SKIP]}}), report(skipped=1), rules="CHECK-001 BFS-003")
visited_fail = model("visited-fail", {"s": {"edges": [edge("tick", "s", checks=[BAD])]}})
bfs("failing-edge-to-visited-state", visited_fail,
    report("failure_found", t=1, d=1, failure=failure(["tick"], ("transition", BAD))), config(1), "BFS-004 CHECK-001")
new_fail = model("cap-failure", {"s": {"edges": [edge("go", "a")]}, "a": {"checks": [BADSTATE]}})
bfs("failure-before-state-admission-cap", new_fail,
    report("failure_found", t=1, d=1, failure=failure(["go"], ("state", BADSTATE))), config(1), "BFS-004 BFS-005")
bfs("passing-successor-at-state-cap", CHAIN, report("state_limit", t=1, d=1), config(1), "BFS-005")
bfs("visited-successor-at-state-cap", LOOP, report(t=1, d=1), config(1), "BFS-004 BFS-005")
bfs("depth-zero-with-unexamined-input", LOOP, report("depth_bound"), config(d=0), "BFS-006")
bfs("terminal-state-at-depth-bound", CHAIN, report(s=2, t=1, d=1), config(d=1), "BFS-006")
cycle = model("cycle", {"s": {"edges": [edge("go", "a")]}, "a": {"edges": [edge("back", "s")]}})
bfs("unexecuted-cycle-edge-at-depth-bound", cycle, report("depth_bound", s=2, t=1, d=1), config(d=1), "BFS-006")
bfs("transition-zero-with-input", LOOP, report("transition_limit"), config(t=0), "BFS-007")
bfs("exact-transition-cap-can-exhaust", CHAIN, report(s=2, t=1, d=1), config(t=1), "BFS-007")
branch = model("branch", {"s": {"edges": [edge("left", "a"), edge("right", "b")]}, "a": {}, "b": {}})
bfs("transition-cap-before-next-edge", branch, report("transition_limit", s=2, t=1, d=1), config(t=1), "BFS-007")
duplicates = model("duplicate", {"s": {"edges": [edge("go", "a"), edge("go", "a")]}, "a": {}})
bfs("duplicate-inputs-are-executed", duplicates, report(s=2, t=2, d=1), rules="MODEL-002 BFS-004")
bfs("hash-collisions-keep-distinct-states", branch, report(s=3, t=2, d=1), rules="MODEL-001 BFS-008")
ordered = model("ordered-bfs", {
    "s": {"edges": [edge("left", "a"), edge("right", "b")]},
    "a": {"edges": [edge("deeper", "c")]},
    "b": {"edges": [edge("shallow-fail", "b", checks=[BAD])]},
    "c": {"edges": [edge("deep-fail", "c", checks=[BAD])]}})
bfs("breadth-first-witness-and-tie-order", ordered,
    report("failure_found", s=4, t=4, d=2, failure=failure(["right", "shallow-fail"], ("transition", BAD))), rules="BFS-001 BFS-002")
both_fail = model("both-fail", {"s": {"edges": [edge("go", "a", checks=[BAD])]}, "a": {"checks": [BADSTATE]}})
bfs("state-failure-before-edge-failure", both_fail,
    report("failure_found", t=1, d=1, failure=failure(["go"], ("state", BADSTATE), ("transition", BAD))), rules="CHECK-002 BFS-004")
skip_loop = model("skip-loop", {"s": {"checks": [SKIP], "edges": [edge("tick", "s", checks=[SKIP])]}})
bfs("skips-counted-on-revisited-state-and-edge", skip_loop, report(t=1, d=1, skipped=3), rules="CHECK-001 BFS-004")
for name, m in [
    ("initial-callback", model("error", {"s": {}}, initial_error=True)),
    ("initial-check", model("error", {"s": {"check_error": True}})),
    ("input-domain", model("error", {"s": {"inputs_error": True}})),
    ("step", model("error", {"s": {"edges": [edge("go", "s", step_error=True)]}})),
    ("successor-check", model("error", {"s": {"edges": [edge("go", "a")]}, "a": {"check_error": True}})),
    ("transition-check", model("error", {"s": {"edges": [edge("go", "s", check_error=True)]}})),
    ("transition-check-after-state-failure", model("error", {"s": {"edges": [edge("go", "a", check_error=True)]}, "a": {"checks": [BADSTATE]}})),
]:
    bfs("model-error-" + name, m, {"error": "model_error"}, rules="ERROR-001")


def identity(m):
    return cp(m.get("metadata", dict(name=m["id"], model_version=1, properties_version=1, codec_version=1, build="fixture-v1")))


def trace(m, steps=(), initial_checks=(), termination="completed", error="", initial="s"):
    return dict(format="stateless.trace-json", version=1, metadata=identity(m),
                initial_state=initial.encode().hex(), initial_checks=list(initial_checks), steps=list(steps),
                termination=termination, error=error)


def step(input, state, outputs=(), checks=(), kind="accepted", reason=""):
    return dict(input=input.encode().hex(), disposition=dict(kind=kind, reason=reason),
                outputs=[x.encode().hex() for x in outputs], post_state=state.encode().hex(), checks=list(checks))


def rec(id, m, inputs, expected, maximum=100, rules="EXEC-001 RECORD-001"):
    add(id, rules, request("record", m, inputs=inputs, max_steps=maximum), {"trace": expected})


rec("record-empty-at-zero-cap", EMPTY, [], trace(EMPTY), 0)
rec("record-nonempty-at-zero-cap", CHAIN, ["go"], trace(CHAIN, termination="step_limit"), 0, "RECORD-002")
CHAIN_TRACE = trace(CHAIN, [step("go", "a")])
rec("record-exact-input-budget-is-complete", CHAIN, ["go"], CHAIN_TRACE, 1, "RECORD-002")
rec("record-extra-input-is-step-limit", LOOP, ["tick", "tick"], trace(LOOP, [step("tick", "s")], termination="step_limit"), 1, "RECORD-002")
init_fail = model("init-fail", {"s": {"checks": [BADSTATE]}})
INIT_TRACE = trace(init_fail, initial_checks=[BADSTATE], termination="property_failed")
rec("record-initial-failure-stops-before-invalid-input", init_fail, ["not-an-input"], INIT_TRACE, 0)
FAIL_TRACE = trace(visited_fail, [step("tick", "s", checks=[BAD])], termination="property_failed")
rec("record-stops-after-first-failure", visited_fail, ["tick", "invalid"], FAIL_TRACE, rules="RECORD-001 CHECK-001")
ordered_checks = [check("z"), check("a"), check("z", "skipped", "second observation")]
rich = model("rich", {"s": {"checks": [check("initial")], "edges": [edge("go", "a", outputs=["z", "a", "z", "", "💾\u0000"], checks=ordered_checks)]},
                      "a": {"checks": [check("state")]}})
RICH_TRACE = trace(rich, [step("go", "a", ["z", "a", "z", "", "💾\u0000"], [check("state")] + ordered_checks)], [check("initial")])
rec("record-preserves-output-and-check-order", rich, ["go"], RICH_TRACE, rules="MODEL-002 CHECK-002 CODEC-001")
for kind in ("rejected", "ignored"):
    m = model(kind, {"s": {"edges": [edge("go", "a", outputs=["effect"], disposition=dict(kind=kind, reason="because"))]}, "a": {}})
    rec("no-framework-policy-for-" + kind, m, ["go"], trace(m, [step("go", "a", ["effect"], kind=kind, reason="because")]), rules="MODEL-003")
error_model = model("late-error", {"s": {"edges": [edge("go", "a")]}, "a": {"edges": [edge("bad", "a", step_error=True)]}})
ERROR_TRACE = trace(error_model, [step("go", "a")], termination="model_error", error="transition: injected step")
rec("record-error-keeps-only-coherent-prefix", error_model, ["go", "bad"], ERROR_TRACE, rules="ERROR-001 RECORD-003")
add("record-initial-error-is-not-a-trace", "ERROR-001 RECORD-003", request("record", model("error", {"s": {}}, initial_error=True), inputs=[], max_steps=1), {"error": "model_error"})


def replay_result(outcome="exact", n=1, reproduced=False, build=True, at=None, field=None):
    return dict(outcome=outcome, steps_verified=n, failure_reproduced=reproduced, build_matches=build, step=at, field=field)


def rep(id, m, t, expected, allow=False, rules="REPLAY-001"):
    add(id, rules, request("replay", m, trace=cp(t), allow_build_mismatch=allow), expected)


rep("replay-exact", CHAIN, CHAIN_TRACE, replay_result())
rep("replay-failure-reproduced", visited_fail, FAIL_TRACE, replay_result(reproduced=True), rules="REPLAY-002")
rep("replay-initial-failure", init_fail, INIT_TRACE, replay_result(n=0, reproduced=True), rules="REPLAY-002")
rep("replay-error-prefix-does-not-execute-error", error_model, ERROR_TRACE, replay_result(), rules="REPLAY-001 RECORD-003")
rep("replay-bounded-empty-prefix", CHAIN, trace(CHAIN, termination="step_limit"), replay_result(n=0), rules="REPLAY-001 RECORD-002")
m = cp(CHAIN); m["initial_error"] = True
rep("replay-never-calls-initial-state", m, CHAIN_TRACE, replay_result(), rules="REPLAY-003")
for key in ("name", "model_version", "properties_version", "codec_version", "build"):
    m = cp(CHAIN); m["metadata"] = identity(m)
    m["metadata"][key] = "changed" if key in ("name", "build") else 2
    rep("replay-rejects-identity-" + key, m, CHAIN_TRACE, replay_result("incompatible", n=0, build=key != "build"), rules="REPLAY-004")
    if key != "build":
        rep("allow-build-does-not-bypass-" + key, m, CHAIN_TRACE, replay_result("incompatible", n=0), allow=True, rules="REPLAY-004")
m = cp(CHAIN); m["metadata"] = identity(m); m["metadata"]["build"] = "other-build"
rep("explicit-build-mismatch-remains-visible", m, CHAIN_TRACE, replay_result(build=False), allow=True, rules="REPLAY-004")
m = cp(rich); m["states"][0]["edges"][0]["outputs"].reverse()
rep("replay-output-order-divergence", m, RICH_TRACE, replay_result("diverged", n=0, at=1, field="outputs"), rules="MODEL-002 REPLAY-005")
m = cp(CHAIN); m["states"][0]["edges"][0]["to"] = "s"
rep("replay-state-divergence", m, CHAIN_TRACE, replay_result("diverged", n=0, at=1, field="state"), rules="REPLAY-005")
m = cp(CHAIN); m["states"][0]["edges"][0]["disposition"] = dict(kind="rejected", reason="changed")
rep("replay-disposition-divergence", m, CHAIN_TRACE, replay_result("diverged", n=0, at=1, field="disposition"), rules="REPLAY-005")
m = cp(CHAIN); m["states"][0]["checks"] = [check("new")]
rep("replay-initial-check-divergence", m, CHAIN_TRACE, replay_result("diverged", n=0, field="initial checks"), rules="REPLAY-005")
m = cp(visited_fail); m["states"][0]["edges"][0]["checks"][0]["details"] = "different details"
rep("failure-can-reproduce-with-detail-divergence", m, FAIL_TRACE, replay_result("diverged", n=0, reproduced=True, at=1, field="checks"), rules="REPLAY-002 REPLAY-005")
for name, t in [("initial-failure-continuation", dict(INIT_TRACE, steps=[step("tick", "s")])),
                ("edge-failure-continuation", dict(FAIL_TRACE, steps=FAIL_TRACE["steps"] * 2)),
                ("false-failure-termination", dict(CHAIN_TRACE, termination="property_failed")),
                ("hidden-failure-termination", dict(FAIL_TRACE, termination="completed"))]:
    m = init_fail if name.startswith("initial") else visited_fail if "edge" in name or "hidden" in name else CHAIN
    rep("reject-fabricated-" + name, m, t, {"error": "model_error"}, rules="REPLAY-006")
t = cp(CHAIN_TRACE); t["steps"][0]["input"] = "ff"
rep("replay-malformed-utf8-input", CHAIN, t, {"error": "model_error"}, rules="CODEC-001")
t = cp(CHAIN_TRACE); t["initial_state"] = "ff"
rep("replay-malformed-utf8-checkpoint", CHAIN, t, {"error": "model_error"}, rules="CODEC-001")

observed = dict(state="s", outputs=[], disposition=dict(kind="accepted"))
no_step = model("observe", {"s": {"checks": [check("state")], "edges": [edge("tick", "s", step_error=True, checks=[check("edge")])]}})
for name, seq, every, enabled, expected in [
    ("observation-never-executes-reducer", 1, 1, True, [check("state"), check("edge")]),
    ("observation-periodic-skip", 1, 2, True, [check("stateless.state_checks", "skipped", "periodic checking policy"), check("edge")]),
    ("observation-periodic-due", 2, 2, True, [check("state"), check("edge")]),
    ("observation-disabled-edge-checks", 1, 1, False, [check("state"), check("stateless.transition_checks", "skipped", "disabled by policy")]),
]:
    add(name, "OBSERVE-001 CHECK-003", request("observe", no_step, before="s", input="tick", transition=observed, sequence=seq, policy=dict(state_every=every, transition_checks=enabled)), dict(checks=expected, step_calls=0))

# Fixed vectors calculated directly from the SplitMix64 definition, not captured
# from either engine. The first seed-zero word is the standard e220a8397b1dcdaf.
add("splitmix64-seed-zero", "RNG-001 RNG-002", request("rng", seed="0", draws=5, bounds=["0", "1", "2", "3", "9223372036854775809", "18446744073709551615"]),
    dict(raw=["16294208416658607535", "7960286522194355700", "487617019471545679", "17909611376780542444", "1961750202426094747"], indices=[], next=""))
# Fill only these mathematical vectors with an independent, stateless word-at-n
# calculation. This helper is not a generator/search implementation under test.
def word(seed, offset):
    x = (seed + offset * 0x9e3779b97f4a7c15) % (1 << 64)
    x = ((x ^ (x >> 30)) * 0xbf58476d1ce4e5b9) % (1 << 64)
    x = ((x ^ (x >> 27)) * 0x94d049bb133111eb) % (1 << 64)
    return x ^ (x >> 31)


def vector(seed, draws, bounds):
    raw = [str(word(seed, i)) for i in range(1, draws + 1)]
    position = draws
    indices = []
    for upper in bounds:
        if upper == 0:
            indices.append(None)
            continue
        threshold = (1 << 64) % upper
        position += 1
        while word(seed, position) < threshold:
            position += 1
        indices.append(str(word(seed, position) % upper))
    return dict(raw=raw, indices=indices, next=str(word(seed, position + 1)))

bounds = [0, 1, 2, 3, (1 << 63) + 1, (1 << 64) - 1]
expected = vector(0, 5, bounds)
assert expected["raw"] == CASES[-1]["expected"]["raw"]
CASES[-1]["expected"] = expected
for seed in (1, (1 << 64) - 1):
    add("splitmix64-seed-" + str(seed), "RNG-001 RNG-002", request("rng", seed=str(seed), draws=0, bounds=[str(x) for x in bounds]), vector(seed, 0, bounds))
add("empty-rng-domain-consumes-nothing", "RNG-002", request("rng", seed="0", draws=0, bounds=["0", "0"]), dict(raw=[], indices=[None, None], next="16294208416658607535"))

# Validation failures are separate from callback failures or property findings.
add("unsupported-protocol-version", "PROTOCOL-001", dict(version=2, operation="hello"), {"error": "unsupported_version"})
add("unsupported-capability-is-not-pass", "PROFILE-001", dict(version=1, operation="fuzz"), {"error": "unsupported_operation"})
for name, req in [
    ("zero-state-budget", request("enumerate", EMPTY, config=config(0))),
    ("zero-observation-sequence", request("observe", no_step, before="s", input="tick", transition=observed, sequence=0, policy=dict(state_every=1, transition_checks=True))),
    ("zero-check-period", request("observe", no_step, before="s", input="tick", transition=observed, sequence=1, policy=dict(state_every=0, transition_checks=True))),
]:
    add(name, "PROTOCOL-002", req, {"error": "invalid_config"})
for name, req in [
    ("unknown-request-field", dict(version=1, operation="hello", extra=True)),
    ("boolean-version", dict(version=True, operation="hello")),
    ("boolean-budget", request("enumerate", EMPTY, config=config(True))),
    ("negative-budget", request("enumerate", EMPTY, config=config(t=-1))),
    ("float-budget", request("enumerate", EMPTY, config=config(t=1.0))),
    ("bad-seed-leading-zero", request("rng", seed="00", draws=0, bounds=[])),
    ("bad-seed-overflow", request("rng", seed="18446744073709551616", draws=0, bounds=[])),
    ("bad-bound-overflow", request("rng", seed="0", draws=0, bounds=["18446744073709551616"])),
    ("unknown-initial-state", request("enumerate", dict(EMPTY, initial="missing"), config=config())),
    ("unknown-successor", request("enumerate", model("bad", {"s": {"edges": [edge("go", "missing")]}}), config=config())),
    ("duplicate-state-ID", request("enumerate", dict(EMPTY, states=[dict(id="s"), dict(id="s")]), config=config())),
    ("conflicting-input-transition", request("enumerate", model("bad", {"s": {"edges": [edge("go", "s"), edge("go", "a")]}, "a": {}}), config=config())),
]:
    add(name, "PROTOCOL-002 MODEL-002", req, {"error": "invalid_request"})
for name, key, value in [("trace-version", "version", 2), ("boolean-trace-version", "version", True),
                         ("trace-kind", "format", "something-else"), ("odd-hex", "initial_state", "f"),
                         ("uppercase-hex", "initial_state", "7A"), ("unknown-termination", "termination", "success")]:
    t = cp(CHAIN_TRACE); t[key] = value
    rep("invalid-" + name, CHAIN, t, {"error": "invalid_request"}, rules="CODEC-001 PROTOCOL-002")


def write():
    result = dict(corpus_version=1, spec_version="1.0.0", profiles=["core-v1", "bfs-v1", "rng-splitmix64-v1", "trace-json-v1"], cases=CASES)
    # One case per physical line makes review diffs small while keeping fixtures
    # executable JSON. Expected output object-key order is intentionally irrelevant.
    header = json.dumps({k: v for k, v in result.items() if k != "cases"})[:-1]
    text = header + ', "cases": [\n' + ',\n'.join(json.dumps(c, ensure_ascii=True, separators=(",", ":")) for c in CASES) + '\n]}\n'
    (ROOT / "corpus.json").write_text(text, encoding="utf-8")
    print(f"Wrote {len(CASES)} golden cases")


if __name__ == "__main__":
    write()
