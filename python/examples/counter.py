"""Run after installing ./python; emits a replayable three-input counter failure."""
from dataclasses import dataclass
from statelessness import Check, Metadata, Transition, enumerate_states, record, replay
from statelessness.trace_json import dumps, loads


@dataclass(frozen=True)
class State:
    count: int = 0


class Counter:
    def metadata(self):
        return Metadata("example-counter", build="example-v1-not-a-content-fingerprint")
    def initial_state(self):
        return State()
    def inputs(self, state):
        return ["increment"]
    def step(self, state, input):
        if input != "increment":
            raise ValueError("unknown input")
        return Transition(State(state.count + 1), ("changed",))
    def check_state(self, state):
        return [Check("count.bound") if state.count <= 2 else Check("count.bound", "failed", "above two")]
    def encode_state(self, state):
        return str(state.count).encode("ascii")
    def decode_state(self, data):
        return State(int(data.decode("ascii")))
    def encode_input(self, input):
        return input.encode("ascii")
    def decode_input(self, data):
        return data.decode("ascii")
    def encode_output(self, output):
        return output.encode("ascii")


def main():
    model = Counter()
    found = enumerate_states(model)
    assert found.failure is not None
    trace = record(model, found.failure.inputs)
    encoded = dumps(trace)
    verified = replay(model, loads(encoded))
    assert verified.outcome == "exact" and verified.failure_reproduced
    print(encoded)


if __name__ == "__main__":
    main()
