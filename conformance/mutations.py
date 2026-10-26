"""Demonstrate that the corpus rejects eight deliberately broken Python engines.

Every mutation changes a unique engine source site in an isolated temporary copy.
A syntax error, crash, timeout, or missing report does NOT count as detection.
The corpus must return normal structured mismatches. Original source is untouched.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
# Keep exact, unique anchors: refactors must fail closed until controls are reviewed.
MUTATIONS = {
    "omit_initial_checks": ("core.py",
        '_dispatch_checks(model, "check_state_into", "check_state", (_copy(state),), check_storage, "initial check")',
        'pass  # deliberately omit initial checker dispatch'),
    "omit_transition_checks": ("core.py",
        '_dispatch_checks(model, "check_transition_into", "check_transition",\n'
        '                             (_copy(before), _copy(input), _copy(transition)), checks,\n'
        '                             "transition check", optional=True)',
        'pass  # deliberately omit transition checker dispatch'),
    "ignore_state_cap": ("core.py", 'if len(nodes) == config.max_states:', 'if False:'),
    "sort_ordered_outputs": ("core.py", 'object.__setattr__(self, "outputs", tuple(self.outputs))', 'object.__setattr__(self, "outputs", tuple(sorted(self.outputs)))'),
    "accept_incompatible_identity": ("replay_evidence.py", 'return replace(report, outcome="incompatible")', 'pass'),
    "misreport_depth_exhaustion": ("core.py", 'return report("depth_bound" if cutoff else "graph_exhausted")', 'return report("graph_exhausted")'),
    "rng_modulo_bias": ("core.py", 'if value >= threshold:', 'if True:'),
    "hide_skipped_checks": ("core.py",
        'check_observed_into(model, before, input, transition, sequence, values, policy)\n    return tuple(values)',
        'check_observed_into(model, before, input, transition, sequence, values, policy)\n'
        '    return tuple(c for c in values if c.status != "skipped")'),
}


def mutate_source(name, original):
    _, old, new = MUTATIONS[name]
    if original.count(old) != 1:
        raise RuntimeError(f"mutation site changed: {name}; review the control")
    return original.replace(old, new, 1)


def main():
    reports = []
    source = ROOT / "python" / "src" / "statelessness"
    for name, (filename, _, _) in MUTATIONS.items():
        original = (source / filename).read_text(encoding="utf-8")
        mutated = mutate_source(name, original)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "statelessness"
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
            (target / filename).write_text(mutated, encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=directory, PYTHONDONTWRITEBYTECODE="1")
            run = subprocess.run([sys.executable, "-m", "statelessness.conformance", str(ROOT / "conformance" / "corpus.json")], env=env,
                                 capture_output=True, text=True, timeout=30, cwd=directory)
            if run.returncode != 1:
                raise RuntimeError(f"mutation {name} did not produce a normal failing suite: {run.returncode}\n{run.stdout}\n{run.stderr}")
            result = json.loads(run.stdout)
            if not result["failures"]:
                raise RuntimeError(f"mutant survived: {name}")
            reports.append(dict(mutation=name, detected_by=[x["id"] for x in result["failures"]]))
    print(json.dumps(dict(mutants=len(reports), detected=len(reports), results=reports), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
