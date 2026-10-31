"""Experimental assurance around an actual finite BFS run."""
from statelessness import (
    AssuranceCoverGoal, AssuranceInputFamily, AssuranceManifest,
    AssuranceObserver, AssuranceProvenance, AssuranceRequiredProperty,
    Check, Metadata, Transition, assurance_search_outcome, enumerate_states,
)


class Counter:
    def metadata(self): return Metadata('counter')
    def initial_state(self): return 0
    def inputs(self, state): return ('increment',) if state < 2 else ()
    def step(self, state, input): return Transition(state + 1)
    def check_state(self, state): return (Check('bounded'),)


class Coverage:
    def observe(self, before, input, after, history, hits):
        hits.family('increment')
        if after.state == 2: hits.goal('reached-two')


def main():
    model = Counter()
    manifest = AssuranceManifest(
        'counter', AssuranceProvenance('catalog-v1', 'counter-v1', 'checks-v1'),
        required_properties=(AssuranceRequiredProperty('bounded', 'state'),),
        input_families=(AssuranceInputFamily('increment'),),
        cover_goals=(AssuranceCoverGoal('reached-two'),),
    )
    observer = AssuranceObserver(manifest, model.metadata(), Coverage())
    search = enumerate_states(model, observer=observer)
    report = observer.finish(assurance_search_outcome(search.termination), search.termination)
    assessment = report.assess()
    print('adequate:', assessment.adequate)
    if not assessment.adequate:
        print('reasons:', assessment.reasons)
    return assessment.adequate


if __name__ == '__main__':
    if not main(): raise SystemExit(1)
