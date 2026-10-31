"""Independent seed/range/admission inventory; imports no engine or adapter."""
from copy import deepcopy

U64 = (1 << 64) - 1
SAFE = (1 << 53) - 1


def splitmix(seed):
    word = (seed + 0x9E3779B97F4A7C15) & U64
    word = ((word ^ (word >> 30)) * 0xBF58476D1CE4E5B9) & U64
    word = ((word ^ (word >> 27)) * 0x94D049BB133111EB) & U64
    return word ^ (word >> 31)


def seeds():
    pairs = [(0, 0), (0, 1), (0, 2), (0, 7), (0, 42), (0, U64),
             (U64, 0), (U64, 1), (1, U64), (U64, 2), (2, U64),
             (1 << 63, 1 << 63), (1 << 63, (1 << 63) + 1),
             (1 << 53, U64 - (1 << 53) + 1), ((1 << 53) + 1, U64 - (1 << 53))]
    return [dict(schema="native-campaign-v1", kind="seed", case=f"seed-{i:02}",
                 master_seed=str(master), job_id=str(job), seed=str(splitmix((master + job) & U64)))
            for i, (master, job) in enumerate(pairs)]


def validation():
    base = dict(master_seed="0", first_job="7", jobs="1", workers="1", result_buffer="0",
                fuzz=dict(seed="999", cases="1", max_steps="2", max_transitions="2", mutation_percent="0"),
                stop_on_failure=False)
    specs = [
        ("jobs-zero", {"jobs": 0}, {}, "campaign jobs must be positive"),
        ("workers-zero", {"workers": 0}, {}, "campaign workers must be in 1..=256"),
        ("workers-257", {"workers": 257}, {}, "campaign workers must be in 1..=256"),
        ("buffer-1025", {"result_buffer": 1025}, {}, "campaign result_buffer must be in 0..=1024"),
        ("workers-256", {"workers": 256}, {}, None),
        ("buffer-1024", {"result_buffer": 1024}, {}, None),
        ("max-id", {"first_job": U64}, {}, None),
        ("id-overflow", {"first_job": U64, "jobs": 2}, {}, "campaign job ID range overflows u64"),
        ("mutation-101", {}, {"mutation_percent": 101}, "mutation_percent must be in 0..=100"),
        ("mutation-100", {}, {"mutation_percent": 100}, None),
        ("aggregate-cases", {"jobs": 2049}, {"cases": SAFE}, "campaign aggregate cases overflow u64"),
        ("aggregate-transitions", {"jobs": 2049}, {"cases": 1, "max_steps": SAFE, "max_transitions": U64}, "campaign aggregate transitions overflow u64"),
        ("wide-product-capped", {"jobs": 2}, {"cases": SAFE, "max_steps": SAFE, "max_transitions": 1}, None),
        ("max-job-count-valid", {"first_job": 0, "jobs": SAFE}, {"cases": 1, "max_steps": 1, "max_transitions": 1}, None),
        ("validation-first-error", {"jobs": 0, "workers": 0, "result_buffer": 1025}, {"mutation_percent": 101}, "campaign jobs must be positive"),
    ]
    # Independent arithmetic justifications; never run enormous callback budgets.
    assert 2049 * SAFE > U64
    assert 2 * SAFE <= U64 < SAFE * SAFE
    assert 2 * min(SAFE * SAFE, 1) == 2
    rows = []
    for name, changes, fuzz_changes, expected_error in specs:
        config = deepcopy(base)
        config.update({key: str(value) for key, value in changes.items()})
        config["fuzz"].update({key: str(value) for key, value in fuzz_changes.items()})
        rows.append(dict(schema="native-campaign-v1", kind="validation", case=name, config=config, error=expected_error))
    return rows
