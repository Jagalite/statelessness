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
MUTATIONS = {
    "omit_initial_checks": ('initial_checks = _checks("initial check", model.check_state, _copy(state))', 'initial_checks = ()'),
    "omit_transition_checks": ('return _checks("transition check", callback, _copy(before), _copy(input), _copy(transition))', 'return ()'),
    "ignore_state_cap": ('if len(nodes) == config.max_states:', 'if False:'),
    "sort_ordered_outputs": ('object.__setattr__(self, "outputs", tuple(self.outputs))', 'object.__setattr__(self, "outputs", tuple(sorted(self.outputs)))'),
    "accept_incompatible_identity": ('return ReplayReport("incompatible", build_matches=build_matches)', 'pass'),
    "misreport_depth_exhaustion": ('return report("depth_bound" if cutoff else "graph_exhausted")', 'return report("graph_exhausted")'),
    "rng_modulo_bias": ('if value >= threshold:', 'if True:'),
    "hide_skipped_checks": ('return state_checks + transition_checks', 'return tuple(c for c in state_checks + transition_checks if c.status != "skipped")'),
}


def main():
    reports = []
    source = ROOT / "python" / "src" / "statelessness"
    original = (source / "core.py").read_text(encoding="utf-8")
    for name, (old, new) in MUTATIONS.items():
        if original.count(old) != 1:
            raise RuntimeError(f"mutation site changed: {name}; review the control")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "statelessness"
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
            (target / "core.py").write_text(original.replace(old, new), encoding="utf-8")
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
