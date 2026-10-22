"""Public-API Python pipe host for the finite N12 wire comparison.

Run using an isolated interpreter containing the installed statelessness wheel.
Framing, command parsing, admission, responses, and deduplication belong to the
library's serve function; this host only supplies the model and codecs.
"""
import argparse
import signal
import sys

from statelessness import Check, Disposition, Metadata, ModelError, Transition
from statelessness.debugger import InputPolicy
from statelessness.debugger_protocol import (
    ProtocolAuthority, ProtocolLimits, ProtocolSession, serve,
)
from statelessness.debugger_session import DebugSession


def callback(message):
    sys.stderr.write("CALL " + message + "\n")
    sys.stderr.flush()


class WireCounter:
    def metadata(self):
        return Metadata("n12-wire-counter", 1, 1, 1, "n12-wire-v1")

    def initial_state(self):
        callback("initial")
        return 0

    def decode_input(self, data):
        callback("decode " + data.hex())
        if len(data) != 1 or data[0] > 3:
            raise ModelError("invalid fixture input")
        return data[0]

    def decode_state(self, data):
        if len(data) != 8:
            raise ModelError("invalid fixture state")
        return int.from_bytes(data, "little")

    def encode_input_into(self, value, sink):
        sink.append(bytes((value,)))

    def encode_state_into(self, value, sink):
        sink.append(value.to_bytes(8, "little"))

    def encode_output_into(self, value, sink):
        sink.append(value.to_bytes(8, "little"))

    def step(self, state, input):
        callback(f"step {state} {input}")
        if input == 0:
            return Transition(state, disposition=Disposition("ignored", "fixture ignored"))
        if input == 1:
            return Transition(state + 1, outputs=(state + 1,))
        if input == 2:
            return Transition(state, disposition=Disposition("rejected", "fixture rejection"))
        raise ModelError("private reducer detail")

    def check_state(self, state):
        callback(f"state {state}")
        return (Check("bounded") if state <= 2 else
                Check("bounded", "failed", "private state detail"),)

    def check_transition(self, before, input, transition):
        callback(f"transition {before} {input} {transition.state}")
        return (Check("transition"),)


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False, add_help=False)
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--exact", action="store_true")
    parser.add_argument("--cache-one", action="store_true")
    args = parser.parse_args()
    # Preserve the OS write failure as BrokenPipeError through public serve,
    # so its disconnect cleanup and the final public-session read both run.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    model = WireCounter()
    session = DebugSession(
        "wire\té😀", model,
        InputPolicy("out-of-domain-injection", unrestricted=True),
    )
    protocol = ProtocolSession(
        session, decode_input=model.decode_input,
        encode_output_into=model.encode_output_into,
        authority=ProtocolAuthority(
            deliver_inputs=not args.read_only, read_exact_values=args.exact,
        ),
        limits=ProtocolLimits(max_cache_entries=1 if args.cache_one else 64),
    )
    try:
        serve(protocol, sys.stdin.buffer, sys.stdout.buffer)
    except Exception as error:
        sys.stderr.write(f"transport: {type(error).__name__}: {error}\n")
        sys.stderr.flush()
        return 1
    finally:
        sys.stderr.write(f"FINAL {session.state} {session.revision} {session.sequence}\n")
        sys.stderr.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
