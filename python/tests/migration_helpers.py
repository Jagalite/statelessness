"""Shared models and fixture spellings for the migration-extension tests."""
import json
from pathlib import Path

from statelessness import (Check, GraphSearchOptions, RunLimits, SearchConfig, SearchLimits,
                           Transition, explore_graph)

ROOT = Path(__file__).resolve().parents[2]


def corpus(name):
    document = json.loads((ROOT / "conformance" / name).read_text(encoding="utf-8"))
    assert document["version"] == 1
    return document["cases"]


class Collide(int):
    """An integer state whose hash puts every state in one bucket."""
    def __hash__(self): return 0


class GraphModel:
    """step(state, input) = input with outputs (input * 10,); state checks are
    [ok] plus failed bad_state for fail_state; bad_edge fails on (before, input)
    in fail_edge."""
    def __init__(self, edges, constant=False, fail_state=(), fail_edge=()):
        self.edges, self.constant = edges, constant
        self.fail_state, self.fail_edge = set(fail_state), {tuple(e) for e in fail_edge}
    def wrap(self, value): return Collide(value) if self.constant else value
    def initial_state(self): return self.wrap(0)
    def inputs(self, state): return list(self.edges.get(int(state), ()))
    def step(self, state, input): return Transition(self.wrap(input), (input * 10,))
    def check_state(self, state):
        if int(state) in self.fail_state:
            return (Check("ok"), Check("bad_state", "failed", f"state {int(state)}"))
        return (Check("ok"),)
    def check_transition(self, before, input, transition):
        return (Check("bad_edge", "failed", f"{int(before)}->{input}"),) if (int(before), input) in self.fail_edge else ()


DIAMOND = {0: [1, 2, 1], 1: [3], 2: [3]}
CHAIN = {0: [1], 1: [2], 2: [3], 3: [4]}


def spell_event(e):
    k = e.kind
    if k == "root_admitted": return f"root:{e.node}"
    if k == "expansion_started": return f"expand:{e.node}"
    if k == "edge_evaluated": return f"edge:{e.edge.id}:{e.edge.source}:{e.edge.input}"
    if k == "finding":
        return f"finding:{e.index}:{'-' if e.edge is None else e.edge}:" + ",".join(
            f"{v.phase}/{v.check.id}" for v in e.violations)
    if k == "successor_admitted": return f"admit:{e.edge}:{e.node}"
    if k == "duplicate_edge": return f"dup:{e.edge}:{e.node}"
    if k == "edge_reduced": return f"reduced:{e.edge}:{e.representative}"
    if k == "successor_rejected": return f"reject:{e.edge}:{e.reason}"
    if k == "expansion_finished": return f"finish:{e.node}:{e.status}"
    if k == "layer_completed": return f"layer:{e.depth}"
    if k == "search_finished": return f"end:{e.termination}"
    raise AssertionError(k)


def search(model, config=SearchConfig(), *, max_duration=None, policy=None, retention=None,
           cancel_after=None, **options):
    events, cancelled = [], False
    def sink(event):
        nonlocal cancelled
        text = spell_event(event)
        events.append(text)
        if text == cancel_after: cancelled = True
    fields = dict(options)
    if policy is not None: fields["failure_policy"] = policy
    if retention is not None: fields["retention"] = retention
    report = explore_graph(model, config, limits=SearchLimits(RunLimits(max_duration, lambda: cancelled)),
                           options=GraphSearchOptions(events=sink, **fields))
    return report, events


def targets(report):
    return [str(edge.target) for edge in report.edges]
