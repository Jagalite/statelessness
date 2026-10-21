#!/usr/bin/env python3
"""Build/check the proof kernel, audit its axioms, and compare real Rust behavior.

Only the Python standard library is required. Rust, Cargo, Lean and Lake must
already be installed. All mutations run in disposable copies, never this tree.
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MAX = (1 << 64) - 1
ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}


def run(args: list[str], *, cwd: Path = ROOT, data: str | None = None,
        timeout: int = 300) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(args, cwd=cwd, input=data, text=True, capture_output=True,
                       timeout=timeout, check=False)
    if p.returncode:
        raise RuntimeError(f"Command failed ({p.returncode}): {args}\n{p.stdout}\n{p.stderr}")
    return p


def audit_proofs() -> list[str]:
    source = (HERE / "Assurance.lean").read_text()
    names = re.findall(r"^theorem\s+(\w+)", source, re.MULTILINE)
    if not names:
        raise RuntimeError("No theorems found; refusing an empty proof audit")
    audit = HERE / "results" / "AxiomAudit.lean"
    audit.parent.mkdir(exist_ok=True)
    audit.write_text("import Assurance\n" + "\n".join(
        f"#print axioms Statelessness.Assurance.{n}" for n in names) + "\n")
    output = run(["lake", "env", "lean", "-DwarningAsError=true", str(audit)], cwd=HERE).stdout
    (HERE / "results" / "axioms.txt").write_text(output)
    for n in names:
        full = "Statelessness.Assurance." + n
        pattern = (rf"'{re.escape(full)}' (?:does not depend on any axioms|"
                   rf"depends on axioms:\s*\[([^]]*)\])")
        matches = list(re.finditer(pattern, output))
        if len(matches) != 1:
            raise RuntimeError(f"Missing/ambiguous axiom audit for {full}:\n{output}")
        dependencies = {s.strip() for s in (matches[0].group(1) or "").split(",") if s.strip()}
        unexpected = dependencies - ALLOWED_AXIOMS
        if unexpected:
            raise RuntimeError(f"Untrusted proof assumptions in {full}: {unexpected}")
    print(f"Kernel-checked and axiom-audited {len(names)} theorems", flush=True)
    return names


def corpus() -> list[str]:
    rows: list[str] = []
    def add(kind: str, *numbers: int) -> None:
        rows.append(kind + " " + " ".join(map(str, numbers)))
    for counts in itertools.product([0, 1, 2, MAX - 1, MAX], repeat=4):
        for candidate in range(4):
            add("C", *counts, candidate)
    for counts in itertools.product([0, 1, MAX], repeat=8):
        add("M", *counts)
    # Independently varied policy, completion, coverage, and error gates.
    for config in itertools.product(range(4), range(2), range(3), range(2),
                                    range(8), range(32), range(2)):
        add("R", *config, 0, 1, 1, 1)
    baseline = (3, 1, 1, 0, 0, 5, 0, 0)
    # Zero, one and two transitions. Both phases deliberately use the same ID.
    for length, alphabet in [(1, range(10)), (3, range(10)), (5, [0, 1, 2, 3, 4, 8])]:
        for batches in itertools.product(alphabet, repeat=length):
            add("R", *baseline, *batches)
    for tamper in range(1, 13):
        add("R", *baseline[:-1], tamper, 1, 1, 1)
    rng = random.Random(20261021)
    for _ in range(2000):
        config = (rng.randrange(4), rng.randrange(2), rng.randrange(3),
                  rng.randrange(2), rng.randrange(8), rng.randrange(32), rng.randrange(2), 0)
        batches = [rng.randrange(10) for _ in range(1 + 2 * rng.randrange(11))]
        add("R", *config, *batches)
    return rows


def outputs(executable: Path, rows: list[str]) -> list[str]:
    output = run([str(executable)], data="\n".join(rows) + "\n").stdout.splitlines()
    if len(output) != len(rows):
        raise RuntimeError(f"{executable} returned {len(output)} rows, expected {len(rows)}")
    return output


def build_rust(root: Path, target: Path) -> Path:
    run(["cargo", "build", "--offline", "--manifest-path",
         str(root / "formal/lean4/rust/Cargo.toml"), "--target-dir", str(target)])
    return target / "debug" / "lean-assurance-bridge"


def mutation_checks(rows: list[str], expected: list[str]) -> list[dict[str, object]]:
    # A build failure is NOT a killed mutant: each must compile, run, and disagree.
    mutations = [
        ("erase_missing_observations",
         "counts.missing = counts.missing.saturating_add(1);",
         "counts.missing = 0;"),
        ("hide_undeclared_bad_checks",
         ".any(|c| c.failed > 0 || c.skipped > 0 || c.missing > 0)",
         ".any(|_| false)"),
        ("sampled_as_exhaustive",
         "RequiredSearchOutcome::GraphExhausted => {\n                self.search.outcome == SearchOutcome::GraphExhausted\n            }",
         "RequiredSearchOutcome::GraphExhausted => {\n                self.search.outcome.completed()\n            }"),
        ("wrap_missing_counter",
         "self.missing = self.missing.saturating_add(other.missing);",
         "self.missing = self.missing.wrapping_add(other.missing);"),
    ]
    receipts = []
    for name, before, after in mutations:
        with tempfile.TemporaryDirectory(prefix="statelessness-lean-mutant-") as directory:
            clone = Path(directory) / "repo"
            shutil.copytree(ROOT, clone, ignore=shutil.ignore_patterns(
                ".git", "target", ".lake", "results", "node_modules", "__pycache__"))
            source_path = clone / "src/assurance.rs"
            source = source_path.read_text()
            if source.count(before) != 1:
                raise RuntimeError(f"Mutation {name} no longer uniquely matches; update its fixture")
            source_path.write_text(source.replace(before, after))
            executable = build_rust(clone, ROOT / "target/lean4-mutants")
            actual = outputs(executable, rows)
            failures = [i for i, (a, b) in enumerate(zip(actual, expected)) if a != b]
            if not failures:
                raise RuntimeError(f"Mutation survived: {name}")
            first = failures[0]
            receipts.append({"name": name, "mismatches": len(failures),
                             "witness": rows[first], "lean": expected[first], "rust": actual[first]})
            print(f"Rejected compiled mutant {name}: {len(failures)} mismatches", flush=True)
    return receipts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mutations", action="store_true", help="also test four compiled Rust mutants")
    args = parser.parse_args()
    run(["lake", "build", "Assurance", "assurance_check"], cwd=HERE)
    names = audit_proofs()
    rust = build_rust(ROOT, ROOT / "target/lean4-assurance")
    lean = HERE / ".lake/build/bin/assurance_check"
    rows = corpus()
    expected, actual = outputs(lean, rows), outputs(rust, rows)
    for i, (a, b) in enumerate(zip(actual, expected)):
        if a != b:
            raise RuntimeError(f"Conformance mismatch at row {i}: {rows[i]}\nLean: {b}\nRust: {a}")
    for executable in [lean, rust]:
        for malformed in ["C 0 0 0", "C 0 0 0 0 4", "M -1", "R 4 1 1 0 0 5 0 0 1 1 1"]:
            p = subprocess.run([str(executable)], input=malformed + "\n", text=True,
                               capture_output=True, timeout=30, check=False)
            if p.returncode == 0:
                raise RuntimeError(f"{executable} accepted malformed protocol: {malformed}")
    print(f"Rust/Lean conformance: {len(rows)} cases agree; 8 malformed-input checks pass", flush=True)
    receipt = {"theorems": names, "cases": len(rows), "protocol_negative_checks": 8,
               "mutants": mutation_checks(rows, expected) if args.mutations else [],
               "lean": run(["lake", "env", "lean", "--version"], cwd=HERE).stdout.strip(),
               "rust": run(["rustc", "--version"]).stdout.strip()}
    (HERE / "results/receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()
