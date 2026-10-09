"""Optional developer schema check. Requires jsonschema; never a runtime dependency."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
corpus = json.loads((ROOT / "conformance/corpus.json").read_text(encoding="utf-8"))
container = json.loads((ROOT / "spec/corpus.schema.json").read_text(encoding="utf-8"))
request = json.loads((ROOT / "spec/request.schema.json").read_text(encoding="utf-8"))
for schema in (container, request):
    Draft202012Validator.check_schema(schema)
Draft202012Validator(container).validate(corpus)
validator = Draft202012Validator(request)
positive = 0
for case in corpus["cases"]:
    if case["expected"].get("error") not in ("invalid_request", "invalid_config", "unsupported_version", "unsupported_operation"):
        validator.validate(case["request"])
        positive += 1
print(json.dumps(dict(container="valid", positive_requests=positive)))

import runpy
additional = list(runpy.run_path(str(ROOT / "conformance/portability.py"))["cases"]())
for case in additional:
    validator.validate(case["request"])
print(json.dumps(dict(additional_portability_requests=len(additional))))
