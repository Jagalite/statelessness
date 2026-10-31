"""Run with an installed statelessness package: python examples/guided.py."""
from statelessness import (Check, FeedbackMetadata, FuzzConfig, StateFeedback,
                          Transition, guided_fuzz, shrink)


class Counter:
    def initial_state(self): return 0
    def generate(self, state, rng): return 1 if rng.index(4) == 0 else -state
    def step(self, state, input): return Transition(state + input)
    def check_state(self, state):
        return (Check("depth", "failed", "reached five"),) if state >= 5 else ()


def main():
    feedback = StateFeedback(FeedbackMetadata("depth", 1, "counter-example-v1"), lambda state: state)
    result = guided_fuzz(Counter(), feedback,
                         FuzzConfig(seed=42, cases=1000, max_steps=12, mutation_percent=100))
    print(result.termination, result.transitions, "transitions")
    print(result.guidance.unique_features, "features;", len(result.guidance.corpus), "retained prefixes")
    if result.failure:
        print("failure:", shrink(Counter(), result.failure).minimized.inputs)


if __name__ == "__main__":
    main()
