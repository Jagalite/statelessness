"""Prepare an opt-in Go overlay for the external N06 causal probe.

The overlay appends a test-only bridge to an installed module's campaign.go;
it does not alter that source or replace its coordinator/channel implementation.
The resulting executable is instrumented and is not a production-binary proof.
"""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--module-dir", type=Path, required=True)
    p.add_argument("--consumer-dir", type=Path, required=True)
    p.add_argument("--overlay-dir", type=Path, required=True)
    a = p.parse_args()
    module = a.module_dir.resolve(strict=True)
    consumer = a.consumer_dir.resolve(strict=True)
    overlay = a.overlay_dir.resolve(strict=True)
    if module in (consumer, overlay) or module in consumer.parents or module in overlay.parents or consumer == overlay:
        p.error("consumer and overlay must be separate from installed module")
    root = Path(__file__).resolve().parent
    original = (module / "campaign.go").read_bytes()
    bridge = (root / "external_backpressure_probe.go").read_text().split("package statelessness", 1)[1].encode()
    combined = overlay / "campaign.go"
    combined.write_bytes(original + b"\n" + bridge)
    (consumer / "main.go").write_text("package main\nfunc main() {}\n")
    (consumer / "go.mod").write_text("module n06-external-causal-probe\n\ngo 1.23.0\n\nrequire github.com/Jagalite/statelessness/go v0.0.0-n06\n")
    mapping = {"Replace": {str(module / "campaign.go"): str(combined), str(consumer / "n06_external_test.go"): str(root / "external_backpressure_test.go")}}
    (consumer / "overlay.json").write_text(json.dumps(mapping, sort_keys=True) + "\n")
    print(json.dumps({"module": str(module), "original_campaign_sha256": hashlib.sha256(original).hexdigest(), "combined_sha256": hashlib.sha256(combined.read_bytes()).hexdigest(), "bridge_sha256": hashlib.sha256((root / "external_backpressure_probe.go").read_bytes()).hexdigest(), "test_sha256": hashlib.sha256((root / "external_backpressure_test.go").read_bytes()).hexdigest()}, sort_keys=True))


if __name__ == "__main__":
    main()
