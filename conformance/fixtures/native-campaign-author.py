"""Materialize independent N06 JSONL and seal from an immutable author snapshot.

Run through Kyoto. This program never launches or imports a native engine.
The source-bound author imports only its two independent stdlib fixture modules.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2]
FILES = ["conformance/fixtures/native-campaign-author.py",
         "conformance/fixtures/native-campaign-golden.py",
         "conformance/fixtures/native-campaign-inventory.py",
         "conformance/fixtures/native-campaign-v1.schema.json",
         "docs/native-parity/N06-RUST-CONTRACT.md"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    assert workspace == Path("/Users/jagatranvo/.t3/worktrees/stateless/t3-25250336")
    output = args.output.resolve()
    output.relative_to(workspace / "validation/native-parity/n06")
    before = {relative: digest(SOURCE / relative) for relative in FILES}
    for relative, wanted in before.items():
        assert digest(workspace / relative) == wanted, f"source changed since author snapshot: {relative}"
    campaign = load("independent_campaign", SOURCE / FILES[1])
    inventory = load("independent_inventory", SOURCE / FILES[2])
    rows = inventory.seeds() + inventory.validation() + [campaign.row(case) for case in campaign.CASES]
    assert len(rows) == 49 and len({row["case"] for row in rows}) == 49
    assert {kind: sum(row["kind"] == kind for row in rows) for kind in ("seed", "validation", "campaign")} == {"seed": 15, "validation": 15, "campaign": 19}
    encoded = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows).encode()
    # Exercise strict key/type/order equality without consulting an engine.
    campaign.equal([campaign.strict_loads(line) for line in encoded.decode().splitlines()], rows)
    assert before == {relative: digest(SOURCE / relative) for relative in FILES}
    output.mkdir(parents=True, exist_ok=False)
    expected = output / "expected.jsonl"
    expected.write_bytes(encoded)
    files = {**before, str(expected.relative_to(workspace)): digest(expected)}
    seal = dict(schema="n06-independent-spec/1", source_commit="0a26bf5a068e5948d82a3d8bd9eb4063ae85115f",
                candidate_output_used=False,
                derivation="Independent masked-integer SplitMix seed arithmetic, manually authored admission/error inventory, and declared counter callback paths. No native/Rust result output imported.",
                cases=[row["case"] for row in rows], files=files,
                inventories={kind: [row["case"] for row in rows if row["kind"] == kind] for kind in ("seed", "validation", "campaign")})
    freeze = output / "FREEZE.json"
    freeze.write_text(json.dumps(seal, sort_keys=True, indent=2) + "\n")
    print(json.dumps(dict(status="independent-expected-materialized-not-runtime-qualified",
                         expected_path=str(expected.relative_to(workspace)), expected_sha256=digest(expected),
                         freeze_path=str(freeze.relative_to(workspace)), freeze_sha256=digest(freeze), cases=len(rows)), sort_keys=True))


if __name__ == "__main__":
    main()
