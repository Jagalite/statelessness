#!/usr/bin/env python3
"""Exercise the linked debugger executable through real OS pipes (no network).

Run after `cargo build -p statelessness-debug --example debug_stdio --offline`.
Optionally pass a different built executable as the first argument.
"""
import pathlib
import struct
import subprocess
import sys
import urllib.parse

BINARY = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "target/debug/examples/debug_stdio")
MAX_FRAME = 65_536


def decode(data):
    fields = [urllib.parse.unquote(value) for value in data.decode("ascii").split("\t")]
    assert fields[:3] == ["DDBG", "u32:1", "response"], fields
    count = int(fields[10].removeprefix("u64:"))
    assert len(fields) == 11 + count * 2, fields
    return fields, dict(zip(fields[11::2], fields[12::2]))


def request(number, revision, command, *args, config=0, epoch=1, session="request-lifecycle", version=1):
    fields = ["DDBG", f"u32:{version}", "request", f"string:{session}", f"u64:{epoch}",
              f"u64:{number}", f"u64:{revision}", f"u64:{config}", command, *args]
    data = "\t".join(urllib.parse.quote(value, safe="-_.:") for value in fields).encode("ascii")
    return struct.pack(">I", len(data)) + data


def run(arguments, requests):
    process = subprocess.run([str(BINARY), *arguments], input=b"".join(requests),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    data = memoryview(process.stdout)
    responses = []
    while data:
        assert len(data) >= 4, "partial response header"
        size = struct.unpack(">I", data[:4])[0]
        assert 0 < size <= MAX_FRAME and len(data) >= size + 4
        responses.append(decode(bytes(data[4:size + 4])))
        data = data[size + 4:]
    assert len(responses) == len(requests)
    return responses


responses = run([], [
    request(1, 0, "step", "bytes:00"),
    request(1, 0, "step", "bytes:00"),
    request(2, 1, "step", "bytes:01"),
    request(3, 2, "step", "bytes:0201"),
    request(4, 3, "status"),
])
assert responses[0] == responses[1], "duplicate request must return original response"
assert all(fields[8] == "ok" for fields, _ in responses)
assert responses[-1][1]["sequence"] == "u64:3"
assert responses[-1][1]["stop"] == "enum:property_failure"

responses = run(["--fixed"], [
    request(1, 0, "step", "bytes:00"),
    request(2, 1, "step", "bytes:01"),
    request(3, 2, "step", "bytes:0201"),
    request(4, 3, "status"),
])
assert all(fields[8] == "ok" for fields, _ in responses)
assert responses[-1][1]["sequence"] == "u64:3"
assert responses[-1][1]["stop"] == "enum:none"

responses = run(["--read-only"], [
    request(1, 0, "step", "bytes:00"),
    request(2, 0, "watch", "u64:1", "u64:0", "field:ready"),
    request(3, 0, "status", config=1),
])
assert responses[0][0][8:10] == ["error", "unauthorized"]
assert responses[1][0][8] == "ok" and responses[1][0][7] == "u64:1"
assert responses[2][1]["sequence"] == "u64:0"
assert responses[2][1]["capability.input_delivery"] == "bool:false"
assert responses[2][1]["capability.watch_configuration"] == "bool:true"
responses = run(["--export-exact"], [
    request(1, 0, "step", "bytes:00"),
    request(2, 1, "step", "bytes:01"),
    request(3, 2, "step", "bytes:0201"),
    request(4, 3, "snapshot"),
    request(5, 3, "export_trace", "u64:1", "u64:0", "u64:4000"),
])
assert all(fields[8] == "ok" for fields, _ in responses)
export = responses[-1][1]
assert export["export.kind"] == "enum:exact_trace_sensitive"
assert export["export.redacted"] == "bool:false"
assert export["export.complete"] == "bool:true"
artifact = bytes.fromhex(export["export.bytes"].removeprefix("bytes:"))
assert artifact.startswith(b"STLESS\x1a\n")
assert len(artifact) == int(export["export.total_bytes"].removeprefix("u64:"))
responses = run(["--read-only", "--probe-profiles"], [
    request(1, 0, "probe_events", "u64:1", "u64:1", "bool:true"),
    request(2, 0, "probe_configure", "u64:1", "u64:1"),
    request(3, 0, "probe_status", "u64:0", "u64:1"),
    request(4, 0, "status"),
])
assert all(fields[8] == "ok" for fields, _ in responses), responses
assert responses[0][1]["probes.events.returned"] == "u64:1"
assert any(key.endswith(".payload.value") and value == "u64:7"
           for key, value in responses[0][1].items())
assert responses[1][1]["probes.capture_revision"] == "u64:2"
assert responses[1][1]["probes.pending_producers.total"] == "u64:1"
assert responses[2][1]["probes.pending_producers.total"] == "u64:1"
assert responses[-1][1]["sequence"] == "u64:0"


# Complete malformed frames must be rejected without poisoning the next frame.
def frame(payload):
    return struct.pack(">I", len(payload)) + payload


malformed = [
    (b"\xff", "malformed"),
    (b"%", "malformed"),
    (b"DDBG\tu32:1\trequest\tstring:%80\tu64:1\tu64:1\tu64:0\tu64:0\tstatus", "malformed"),
    (b"DDBG\tu32:1\trequest\tstring:request-lifecycle\tu64:1\tu64:+1\tu64:0\tu64:0\tstatus", "malformed"),
    (b"DDBG\tu32:1\trequest\tstring:request-lifecycle\tu64:1\tu64:18446744073709551616\tu64:0\tu64:0\tstatus", "malformed"),
    (b"\t".join([b"x"] * 65), "limit"),
    (b"x" * 8193, "limit"),
    (request(1, 0, "inspect", "u64:1", "u64:1", "u64:0", "u64:1", *["field:x"] * 33)[4:], "limit"),
    (request(1, 0, "step", "bytes:0")[4:], "malformed"),
    (request(1, 0, "step", "bytes:gg")[4:], "malformed"),
    (request(1, 0, "step", "bytes:" + "00" * 8193)[4:], "limit"),
    (request(1, 0, "status", "unexpected")[4:], "malformed"),
    (request(1, 0, "eval", "string:$(touch /tmp/not-executed)")[4:], "unsupported"),
    (request(1, 0, "status", version=2)[4:], "unsupported_version"),
]
for payload, code in malformed:
    bad, good = run([], [frame(payload), request(1, 0, "status")])
    assert bad[0][5] == "u64:0" and bad[0][8:10] == ["error", code], bad
    assert good[0][8] == "ok" and good[1]["sequence"] == "u64:0", good

# A complete hostile envelope cannot mutate or retire an otherwise valid ID.
responses = run([], [
    request(1, 0, "step", "bytes:00", epoch=0),
    request(1, 0, "step", "bytes:00", epoch=99),
    request(1, 0, "step", "bytes:00", session="other\n\x1b[2J<script>"),
    request(1, 0, "status", epoch=0),
    request(2, 0, "step", "bytes:00"),
    request(3, 0, "step", "bytes:00"),
    request(4, 1, "status"),
])
assert [r[0][9] for r in responses[:3]] == ["wrong_epoch", "wrong_epoch", "wrong_session"]
assert responses[3][0][8] == "ok"
assert responses[4][0][8] == "ok"
assert responses[5][0][9] == "stale_revision"
assert responses[-1][1]["sequence"] == "u64:1"

# Cache eviction retires request IDs rather than replaying uncertain mutations.
responses = run([], [request(1, 0, "step", "bytes:00"),
                     *[request(i, 1, "status") for i in range(2, 68)],
                     request(1, 0, "step", "bytes:00"), request(68, 1, "status")])
assert responses[-2][0][9] == "retired_request"
assert responses[-1][1]["sequence"] == "u64:1"
responses = run([], [request(1, 0, "step", "bytes:00"),
                     request(1, 1, "step", "bytes:01"), request(2, 1, "status")])
assert responses[1][0][9] == "duplicate_conflict"
assert responses[-1][1]["sequence"] == "u64:1"

# Snapshot and candidate IDs remain bound to the acknowledged execution revision.
responses = run([], [request(1, 0, "snapshot"), request(2, 0, "inputs", "u64:0", "u64:1"),
                     request(3, 0, "select", "u64:1"),
                     request(4, 1, "select", "u64:1"),
                     request(5, 1, "inspect", "u64:1", "u64:1", "u64:0", "u64:1"),
                     request(6, 1, "status")])
assert responses[2][0][8] == "ok"
assert [responses[i][0][9] for i in [3, 4]] == ["stale_handle", "stale_handle"]
assert responses[-1][1]["sequence"] == "u64:1"

# Failed profile changes never acknowledge or change producer capture state.
responses = run(["--read-only", "--probe-profiles"], [
    request(1, 0, "probe_configure", "u64:0", "u64:1"),
    request(2, 0, "probe_configure", "u64:1", "u64:999"),
    request(3, 0, "probe_events", "u64:999", "u64:1", "bool:true"),
    request(4, 0, "probe_status", "u64:0", "u64:1"),
    request(5, 0, "snapshot"),
    request(6, 0, "export_trace", "u64:1", "u64:0", "u64:100"),
])
assert [r[0][9] for r in responses[:3]] == ["stale_configuration", "unauthorized", "unauthorized"]
assert responses[3][1]["probes.capture_revision"] == "u64:1"
assert responses[3][1]["probes.sinks.0.queued_records"] == "u64:1"
assert responses[5][0][9] == "unauthorized"

# Oversized/zero headers and every truncated prefix are fatal transport errors,
# including when malicious trailing bytes resemble a valid mutation frame.
complete = request(1, 0, "step", "bytes:00")
fatal_inputs = [struct.pack(">I", size) + complete for size in [0, MAX_FRAME + 1, 0xffffffff]]
fatal_inputs += [complete[:cut] for cut in range(1, len(complete))]
for data in fatal_inputs:
    result = subprocess.run([str(BINARY)], input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=10)
    assert result.returncode != 0, (data, result.stdout)
    assert result.stdout == b"", (data, result.stdout)

# Real OS pipes may split both the length prefix and payload across writes.
process = subprocess.Popen([str(BINARY)], stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
try:
    for byte in complete:
        process.stdin.write(bytes([byte]))
        process.stdin.flush()
    process.stdin.close()
    process.stdin = None
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stderr.decode(errors="replace")
    size = struct.unpack(">I", stdout[:4])[0]
    assert len(stdout) == size + 4
    fragmented = decode(stdout[4:])
    assert fragmented[0][8] == "ok" and fragmented[1]["sequence"] == "u64:1"
finally:
    if process.poll() is None:
        process.kill()
        process.communicate()

# An accepted turn remains committed if the next transport frame is broken.
result = subprocess.run([str(BINARY)], input=complete + b"\x00\x01", stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=10)
assert result.returncode != 0
size = struct.unpack(">I", result.stdout[:4])[0]
assert len(result.stdout) == size + 4
committed = decode(result.stdout[4:])
assert committed[0][8] == "ok" and committed[1]["sequence"] == "u64:1"

# Separate process lifetimes require fresh status reconciliation. Do not retry
# old mutations across restart; process-local epochs/deduplication are not durable.
for _ in range(2):
    status = run([], [request(1, 0, "status", epoch=0)])[0]
    assert status[0][8] == "ok" and status[1]["sequence"] == "u64:0"

# Reject unknown startup options before admitting any commands, especially
# near-misses of the authority-reducing --read-only flag. Do not echo raw args.
for option in ["--read-onyl", "--read-only=true", "--export-exact=true", "\x1b[2Jsecret-option"]:
    result = subprocess.run([str(BINARY), option], input=complete, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=10)
    assert result.returncode != 0 and result.stdout == b"", (option, result.stdout)
    assert option.encode() not in result.stderr, result.stderr

print(f"stdio: original workflows plus {len(malformed)} malformed-frame cases, "
      f"{len(fatal_inputs)} fatal pipe cases, byte-at-a-time framing, epoch discovery, stale handles/revisions, "
      "dedup conflict/eviction, sink/profile authorization, raw-export denial, "
      "restart reconciliation, and fail-closed startup options passed")
