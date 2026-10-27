"""External finite application; research calculations belong to the package."""
import dataclasses
import json
from pathlib import Path
import sys
import statelessness
from statelessness import Check, Disposition, Metadata, ModelError, Transition
from statelessness import research


def merged(base, override):
    result = dict(base)
    for key, value in override.items():
        result[key] = merged(result[key], value) if isinstance(value, dict) else value
    return result


class TableModel:
    def __init__(self, scenario, metadata, error_at, log):
        self.scenario, self.identity, self.error_at, self.log = scenario, metadata, error_at, log
        self.rows = {row['state']: row for row in scenario['rows']}

    def event(self, name):
        self.log.append(name)
        if name == self.error_at:
            raise ModelError('fixture error at ' + name)

    def metadata(self):
        self.event('metadata')
        return Metadata(**self.identity)

    def initial_state(self):
        self.event('initial')
        return self.scenario['initial']

    def check_state(self, state):
        self.event(f'state:{state}')
        return tuple(Check(c['id'], c['status'], c.get('details') or '') for c in self.rows[state]['checks'])

    def inputs(self, state):
        self.event(f'inputs:{state}')
        edges = self.rows[state]['inputs']
        owner = self
        class Inputs:
            def __init__(self): self.index = 0
            def __iter__(self): return self
            def __next__(self):
                owner.event(f'next:{state}:{self.index}')
                if self.index == len(edges): raise StopIteration
                edge = edges[self.index]; self.index += 1
                return edge['input']
        return Inputs()

    def edge(self, state, label):
        return next(edge for edge in self.rows[state]['inputs'] if edge['input'] == label)

    def step(self, state, label):
        self.event(f'step:{state}:{label}')
        edge = self.edge(state, label)
        d = edge['disposition']
        return Transition(edge['target'], tuple(edge['outputs']), Disposition(d['kind'], d.get('reason') or ''))

    def check_transition(self, state, label, turn):
        self.event(f'transition:{state}:{label}')
        return tuple(Check(c['id'], c['status'], c.get('details') or '') for c in self.edge(state, label)['checks'])


def normalized(value, name=''):
    if name in ('max_states', 'max_edges', 'max_equivalence_pairs', 'declared_states',
                'materialized_edges', 'compared_pairs', 'edges', 'skipped_checks'):
        return str(value)
    if dataclasses.is_dataclass(value):
        return {f.name: normalized(getattr(value, f.name), f.name) for f in dataclasses.fields(value)}
    if isinstance(value, (tuple, list)): return [normalized(v) for v in value]
    if isinstance(value, dict): return {k: normalized(v) for k, v in value.items()}
    return value


def run_case(defaults, case):
    log = []
    try:
        p = merged(defaults['proposal'], case.get('proposal_overrides', {}))
        proposal = research.Proposal(p['name'], p['version'], p['build'], research.Assumptions(**p['assumptions']),
                                     tuple(research.RequiredProperty(**v) for v in p['required_properties']))
        c = merged(defaults['config'], case.get('config_overrides', {}))
        config = research.ExperimentConfig(**{k: v if k == 'enabled' else int(v) for k, v in c.items()})
        scenario = case['scenario']
        model = TableModel(scenario, defaults['metadata'], case.get('error_at'), log)
        def projection(state):
            model.event(f'projection:{state}')
            return scenario['keys'][scenario['states'].index(state)]
        report = research.compare_equivalence(model, scenario['states'], proposal, config, projection)
        return dict(case=case['case'], error=None, report=normalized(report), callback_log=log,
                    qualification_parameters=research.qualification_parameters(report))
    except Exception as error:
        return dict(case=case['case'], error=str(error), report=None, callback_log=log, qualification_parameters=None)


def main():
    if len(sys.argv) != 4 or sys.argv[1] != 'run': raise SystemExit('usage: consumer.py run CORPUS OUTPUT')
    fixture = json.loads(Path(sys.argv[2]).read_text())
    results = [run_case(fixture['defaults'], case) for case in fixture['cases']]
    Path(sys.argv[3]).write_text(json.dumps(dict(profile='research-equivalence-v1', results=results), ensure_ascii=False) + '\n')
    print(json.dumps(dict(producer='python', mode='run', case_count=len(results), production_pruning_enabled=False,
                         package=str(Path(statelessness.__file__).resolve()))))


if __name__ == '__main__': main()
