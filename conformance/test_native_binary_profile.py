"""Independent fixed binary-v1 wire arithmetic; never imports an engine."""
import copy
import json
from pathlib import Path
import struct
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "conformance/native-binary-v1.json"
HEADER = bytes.fromhex("53544c4553531a0a01000000")
DEFAULT_LIMITS = dict(max_total_bytes=67108864, max_frame_bytes=8388608,
                    max_blob_bytes=4194304, max_string_bytes=65536,
                    max_steps=100000, max_checks_per_step=4096,
                    max_outputs_per_step=4096, max_parameters=1024, max_items=250000)


def u32(n): return struct.pack("<I", n)
def u64(n): return struct.pack("<Q", n)
def sized(b): return u32(len(b)) + b
def text(s): return sized(s.encode("utf-8"))
def blob(h): return sized(bytes.fromhex(h))
def frame(tag, p):
    head = bytes([tag]) + u32(len(p))
    return head + p + u32(zlib.crc32(head + p))


def check_batch(checks):
    result = u32(len(checks))
    for c in checks:
        result += text(c["id"]) + bytes([{"passed": 0, "failed": 1, "skipped": 2}[c["status"]]])
        if c["status"] != "passed": result += text(c["reason"])
    return result


def payloads(t):
    m, c = t["metadata"], t["config"]
    run = text(m["name"]) + u32(m["model_version"]) + u32(m["properties_version"]) + u32(m["codec_version"]) + text(m["build"]) + text(c["strategy"])
    run += b"\0" if c["seed"] is None else b"\1" + u64(int(c["seed"]))
    run += u32(len(c["parameters"])) + b"".join(text(k) + text(v) for k, v in c["parameters"])
    run += blob(t["initial_state"]) + check_batch(t["initial_checks"])
    result = [(1, run)]
    for index, s in enumerate(t["steps"]):
        d = s["disposition"]
        p = u64(index) + blob(s["input"]) + bytes([{"accepted": 0, "rejected": 1, "ignored": 2}[d["kind"]]])
        if d["kind"] != "accepted": p += text(d["reason"])
        p += u32(len(s["outputs"])) + b"".join(blob(h) for h in s["outputs"])
        p += blob(s["post_state"]) + check_batch(s["checks"])
        result.append((2, p))
    end = t["termination"]
    p = u64(len(t["steps"])) + bytes([{"completed": 0, "property_failed": 1, "step_limit": 2, "interrupted": 3, "model_error": 4}[end["kind"]]])
    if end["kind"] == "model_error": p += text(end["reason"])
    result.append((3, p))
    return result


def encode(t): return HEADER + b"".join(frame(tag, p) for tag, p in payloads(t))
def check(id, status="passed", reason=None): return dict(id=id, status=status, reason=reason)
def step(input="01", kind="accepted", reason=None, outputs=None, state="02", checks=None):
    return dict(input=input, disposition=dict(kind=kind, reason=reason), outputs=[] if outputs is None else outputs,
                post_state=state, checks=[] if checks is None else checks)
def empty():
    return dict(metadata=dict(name="", model_version=0, properties_version=0, codec_version=0, build=""),
                config=dict(strategy="manual", seed=None, parameters=[]), initial_state="", initial_checks=[],
                steps=[], termination=dict(kind="completed", reason=None))


def build_corpus():
    fixtures = []
    def valid(name, t, limits=None):
        row = dict(case=name, valid=True, bytes_hex=encode(t).hex(), trace=t)
        if limits is not None: row["limits"] = limits
        fixtures.append(row)
        return bytes.fromhex(row["bytes_hex"])
    def invalid(name, b, kind, detail=None, limits=None):
        row = dict(case=name, valid=False, bytes_hex=b.hex(), expected_error=dict(kind=kind, detail=detail))
        if limits is not None: row["limits"] = limits
        fixtures.append(row)
    t = empty(); base = valid("empty", t)
    one = empty(); one["metadata"].update(name="counter", model_version=1, properties_version=2, codec_version=3, build="b")
    one["initial_state"] = "00"; one["initial_checks"] = [check("ok")]
    one["steps"] = [step(outputs=["", "00ff"], checks=[check("ok"), check("later", "skipped", "not run")])]
    single = valid("single-step", one)
    failed = copy.deepcopy(one); failed["steps"][0]["checks"] = [check("bad", "failed", "broken")]
    failed["termination"] = dict(kind="property_failed", reason=None); valid("property-failed", failed)
    initial = empty(); initial["initial_checks"] = [check("", "failed", "")]
    initial["termination"] = dict(kind="property_failed", reason=None); valid("initial-property-failed", initial)
    unicode = empty(); unicode["metadata"] = dict(name="雪😀", model_version=4294967295, properties_version=4294967295, codec_version=4294967295, build="e\u0301")
    unicode["config"] = dict(strategy="探索", seed="18446744073709551615", parameters=[["k", "α"], ["k", "β"], ["", ""]])
    unicode["initial_state"] = "00ff80"; valid("unicode-config-full-u64", unicode)
    zero = empty(); zero["config"]["seed"] = "0"; valid("zero-seed", zero)
    dispositions = empty(); dispositions["steps"] = [step(kind="rejected", reason="", state="00"), step(input="ff", kind="ignored", reason="忽略", outputs=["ff"], state="00", checks=[check("skip", "skipped", "")])]
    valid("rejected-ignored", dispositions)
    for term in ["step_limit", "interrupted", "model_error"]:
        x = empty(); x["termination"] = dict(kind=term, reason="ошибка😀" if term == "model_error" else None)
        valid(term, x)
    parts = payloads(t)
    def artifact(run=None, steps=None, end=None):
        return HEADER + frame(1, parts[0][1] if run is None else run) + (b"" if steps is None else steps) + frame(3, parts[-1][1] if end is None else end)
    invalid("bad-magic", b"X" + base[1:], "invalid_data", "file magic")
    invalid("unsupported-version", base[:8] + b"\2\0" + base[10:], "unsupported_version", "2")
    invalid("unsupported-flags", base[:10] + b"\1\0" + base[12:], "invalid_data", "unsupported header flags")
    invalid("bad-checksum", base[:-1] + bytes([base[-1] ^ 1]), "checksum_mismatch")
    for name, b in [("short-header", base[:11]), ("missing-footer", base[:12 + 9 + len(parts[0][1])]), ("short-checksum", base[:-1])]:
        invalid(name, b, "truncated")
    invalid("trailing-byte", base + b"\0", "invalid_data", "trailing data after footer")
    invalid("first-record-step", HEADER + frame(2, parts[0][1]) + frame(3, parts[-1][1]), "invalid_data", "first record must be run metadata")
    invalid("unknown-record", HEADER + frame(1, parts[0][1]) + frame(99, b""), "invalid_data", "unknown or misplaced record")
    invalid("duplicate-run", HEADER + frame(1, parts[0][1]) * 2, "invalid_data", "unknown or misplaced record")
    invalid("run-trailing-fields", artifact(run=parts[0][1] + b"\0"), "invalid_data", "trailing record fields")
    invalid("footer-trailing-fields", artifact(end=parts[-1][1] + b"\0"), "invalid_data", "trailing record fields")
    invalid("footer-count", artifact(end=u64(1) + b"\0"), "invalid_data", "footer transition count")
    invalid("termination-tag", artifact(end=u64(0) + b"\5"), "invalid_data", "termination tag")
    invalid("seed-tag", artifact(run=parts[0][1][:30] + b"\2" + parts[0][1][31:]), "invalid_data", "seed tag")
    invalid("invalid-utf8", artifact(run=u32(1) + b"\xff" + parts[0][1][4:]), "invalid_data", "invalid UTF-8")
    invalid("field-outside-frame", artifact(run=u32(100) + parts[0][1][4:]), "invalid_data", "field exceeds frame")
    invalid("oversized-string-length", artifact(run=u32(65537) + parts[0][1][4:]), "limit_exceeded", "string bytes")
    # Empty RUN layout: name len4 + versions12 + build len4 + strategy10 +
    # seed tag1 + parameters count4 + initial blob len4 + checks count4 =43.
    invalid("parameter-count-outside-frame", artifact(run=parts[0][1][:31] + u32(2) + parts[0][1][35:]), "invalid_data", "collection count exceeds frame")
    invalid("check-count-outside-frame", artifact(run=parts[0][1][:-4] + u32(1)), "invalid_data", "collection count exceeds frame")
    invalid("oversized-frame-declaration", HEADER + b"\1" + u32(8388609), "limit_exceeded", "frame bytes")
    sp = payloads(one)[1][1]
    invalid("transition-index", artifact(steps=frame(2, u64(1) + sp[8:]), end=u64(1) + b"\0"), "invalid_data", "transition index")
    # Single-step input is one byte, so disposition is at byte13.
    invalid("disposition-tag", artifact(steps=frame(2, sp[:13] + b"\3" + sp[14:]), end=u64(1) + b"\0"), "invalid_data", "disposition tag")
    cp = u64(0) + sized(b"") + b"\0" + u32(0) + sized(b"") + u32(1) + text("") + b"\3"
    invalid("check-status-tag", artifact(steps=frame(2, cp), end=u64(1) + b"\0"), "invalid_data", "check status tag")
    for name, key, value, data, detail in [
        ("total-limit", "max_total_bytes", len(base)-1, base, "total bytes"),
        ("frame-limit", "max_frame_bytes", len(parts[0][1])-1, base, "frame bytes"),
        ("blob-limit", "max_blob_bytes", 0, single, "blob bytes"),
        ("string-limit", "max_string_bytes", 5, base, "string bytes"),
        ("step-limit", "max_steps", 0, single, "steps"),
        ("check-limit", "max_checks_per_step", 0, single, "checks per step"),
        ("output-limit", "max_outputs_per_step", 1, single, "outputs per step"),
        ("parameter-limit", "max_parameters", 2, encode(unicode), "parameters"),
        ("item-limit", "max_items", 0, single, "aggregate items"),
    ]: invalid(name, data, "limit_exceeded", detail, {key:value})
    valid("exact-total-limit", t, {"max_total_bytes":len(base)})
    valid("exact-frame-limit", t, {"max_frame_bytes":len(parts[0][1])})
    # Append independent positive edge cases; keep earlier fixtures unchanged.
    empty_error = empty(); empty_error["termination"] = dict(kind="model_error", reason="")
    valid("empty-model-error-reason", empty_error)
    unusual_completed = empty(); unusual_completed["initial_checks"] = [check("bad", "failed", "")]
    valid("structural-completed-with-failure", unusual_completed)
    unusual_failed = empty(); unusual_failed["termination"] = dict(kind="property_failed", reason=None)
    valid("structural-property-failed-without-failure", unusual_failed)
    duplicate_checks = empty()
    duplicate_checks["initial_checks"] = [check("duplicate"), check("duplicate", "skipped", "")]
    duplicate_checks["steps"] = [step(checks=[check("duplicate", "failed", "first"), check("duplicate", "failed", "second")])]
    duplicate_checks["termination"] = dict(kind="property_failed", reason=None)
    valid("ordered-duplicate-check-ids", duplicate_checks)
    # Two parameters + two initial checks + one STEP + two outputs + two STEP
    # checks = nine aggregate items. Blob bytes and strings are not items.
    boundary = empty()
    boundary["config"]["parameters"] = [["same", "first"], ["same", "second"]]
    boundary["initial_checks"] = [check("a"), check("b", "skipped", "")]
    boundary["steps"] = [step(outputs=["", "ff"], checks=[check("a"), check("b")])]
    valid("exact-item-limit", boundary, {"max_items":9})
    valid("exact-parameter-limit", boundary, {"max_parameters":2})
    valid("exact-check-limit", boundary, {"max_checks_per_step":2})
    valid("exact-output-limit", boundary, {"max_outputs_per_step":2})
    return dict(profile="native-binary-v1", version=1, status="candidate", defaults=DEFAULT_LIMITS, fixtures=fixtures)


def bitwise_crc(data):
    crc = 0xffffffff
    for byte in data:
        crc ^= byte
        for _ in range(8): crc = (crc >> 1) ^ (0xedb88320 if crc & 1 else 0)
    return crc ^ 0xffffffff


class NativeBinaryProfileTests(unittest.TestCase):
    def test_fixed_corpus_reproducible_without_engine(self):
        self.assertEqual(json.loads(PATH.read_text()), build_corpus())

    def test_valid_frame_sizes_and_independent_bitwise_crc(self):
        self.assertEqual(bitwise_crc(b"123456789"), 0xcbf43926)
        data = json.loads(PATH.read_text())
        for row in data["fixtures"]:
            if not row["valid"]: continue
            encoded = bytes.fromhex(row["bytes_hex"])
            self.assertEqual(encoded[:12], HEADER)
            pos, tags = 12, []
            while pos < len(encoded):
                size = int.from_bytes(encoded[pos+1:pos+5], "little")
                end = pos + 5 + size
                self.assertLessEqual(end+4, len(encoded))
                self.assertEqual(bitwise_crc(encoded[pos:end]), int.from_bytes(encoded[end:end+4], "little"))
                tags.append(encoded[pos]); pos = end+4
            self.assertEqual(tags, [1] + [2]*len(row["trace"]["steps"]) + [3])
            self.assertEqual(pos, len(encoded))

    def test_required_variants_and_limits(self):
        data = json.loads(PATH.read_text()); rows = data["fixtures"]
        self.assertEqual(len(rows), len({r["case"] for r in rows}))
        valid = [r for r in rows if r["valid"]]
        self.assertEqual({r["trace"]["termination"]["kind"] for r in valid}, {"completed", "property_failed", "step_limit", "interrupted", "model_error"})
        self.assertEqual({s["disposition"]["kind"] for r in valid for s in r["trace"]["steps"]}, {"accepted", "rejected", "ignored"})
        self.assertEqual({c["status"] for r in valid for c in r["trace"]["initial_checks"] + [c for s in r["trace"]["steps"] for c in s["checks"]]}, {"passed", "failed", "skipped"})
        self.assertEqual({k for r in rows if not r["valid"] for k in r.get("limits", {})}, set(DEFAULT_LIMITS))
        seeds = {r["trace"]["config"]["seed"] for r in valid}
        self.assertTrue({None, "0", "18446744073709551615"} <= seeds)
        empty_bytes = bytes.fromhex(next(r["bytes_hex"] for r in valid if r["case"] == "empty"))
        self.assertEqual(len(empty_bytes), 12 + 9 + 43 + 9 + 9)

    def test_structural_variants_are_lossless(self):
        rows = {r["case"]: r for r in json.loads(PATH.read_text())["fixtures"]}
        self.assertEqual(rows["empty-model-error-reason"]["trace"]["termination"], {"kind":"model_error", "reason":""})
        completed = rows["structural-completed-with-failure"]["trace"]
        self.assertEqual(completed["termination"]["kind"], "completed")
        self.assertEqual(completed["initial_checks"][0]["status"], "failed")
        failed = rows["structural-property-failed-without-failure"]["trace"]
        self.assertEqual(failed["termination"]["kind"], "property_failed")
        self.assertEqual(failed["initial_checks"], [])
        self.assertEqual(failed["steps"], [])
        duplicates = rows["ordered-duplicate-check-ids"]["trace"]
        self.assertEqual(duplicates["initial_checks"], [check("duplicate"), check("duplicate", "skipped", "")])
        self.assertEqual(duplicates["steps"][0]["checks"], [check("duplicate", "failed", "first"), check("duplicate", "failed", "second")])

    def test_exact_collection_boundaries(self):
        rows = {r["case"]: r for r in json.loads(PATH.read_text())["fixtures"]}
        for name, key, expected in [("item", "max_items", 9), ("parameter", "max_parameters", 2),
                                    ("check", "max_checks_per_step", 2), ("output", "max_outputs_per_step", 2)]:
            row = rows[f"exact-{name}-limit"]
            self.assertTrue(row["valid"])
            self.assertEqual(row["limits"], {key:expected})
            trace = row["trace"]
            self.assertEqual(len(trace["config"]["parameters"]), 2)
            self.assertEqual(len(trace["initial_checks"]), 2)
            self.assertEqual([len(s["checks"]) for s in trace["steps"]], [2])
            self.assertEqual([len(s["outputs"]) for s in trace["steps"]], [2])
            items = len(trace["config"]["parameters"]) + len(trace["initial_checks"])
            items += sum(1 + len(s["checks"]) + len(s["outputs"]) for s in trace["steps"])
            self.assertEqual(items, 9)


if __name__ == "__main__": unittest.main()
