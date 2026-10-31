"""N06 lifecycle contracts and real-core self-model; execution is a separate gate."""
from copy import deepcopy
from itertools import permutations
import unittest

from statelessness import (AuditTrace, Disposition, Failure, FuzzConfig, ModelError,
    RunConfig, SearchConfig, ShrinkConfig, Transition, audit_from_bytes,
    audit_to_bytes, enumerate_states, fuzz, record, replay, shrink)
from statelessness._campaign_lifecycle import Effect, Event, Lifecycle

if __package__:
    from .campaign_lifecycle_fixture import (AuditedLifecycle, LifecycleCodec, OUTCOMES,
        NATIVE, U64_MAX, complete, consume_lifecycle_case, native_event,
        normalize_effect, normalize_event, normalize_state, sink)
else:
    from campaign_lifecycle_fixture import (AuditedLifecycle, LifecycleCodec, OUTCOMES,
        NATIVE, U64_MAX, complete, consume_lifecycle_case, native_event,
        normalize_effect, normalize_event, normalize_state, sink)


class _Assertions:
    def assert_checks(self, checks):
        self.assertEqual([check for check in checks if check.status != "passed"], [])

    def apply(self, model, state, event, accepted=True, effects=()):
        snapshot = deepcopy(state)
        transition = model.step(state, event)
        self.assertEqual(state, snapshot, "snapshot step mutated its predecessor")
        self.assertEqual(transition.disposition.kind, "accepted" if accepted else "rejected")
        self.assertEqual(transition.outputs, effects)
        self.assert_checks(model.check_state(transition.state))
        self.assert_checks(model.check_transition(state, event, transition))
        if not accepted:
            self.assertEqual(transition.state, state)
        return transition.state

    def prefix(self, model, events):
        state = model.initial_state()
        self.assert_checks(model.check_state(state))
        for event in events:
            transition = model.step(state, event)
            self.assertEqual(transition.disposition.kind, "accepted")
            self.assert_checks(model.check_state(transition.state))
            self.assert_checks(model.check_transition(state, event, transition))
            state = transition.state
        return state


class LifecycleTests(_Assertions, unittest.TestCase):
    def test_completion_receipt_and_ack_are_distinct(self):
        model = AuditedLifecycle(jobs=1)
        state = model.initial_state()
        for event in (complete(50), Event("receive", job_id=50), sink(50), Event("finish")):
            self.apply(model, state, event, False)
        state = self.apply(model, state, Event("claim"), effects=(Effect("run_job", 50),))
        state = self.apply(model, state, complete(50))
        self.assertEqual((state.core.report.started, state.core.report.finished,
                          state.core.report.delivered), (1, 0, 0))
        self.apply(model, state, complete(50, "cancelled"), False)
        state = self.apply(model, state, Event("receive", job_id=50), effects=(Effect("deliver", 50),))
        self.assertEqual((state.core.report.finished, state.core.report.delivered), (1, 0))
        self.apply(model, state, Event("finish"), False)
        state = self.apply(model, state, sink(50))
        state = self.apply(model, state, Event("finish"))
        self.assertEqual((state.core.report.finished, state.core.report.delivered), (1, 1))
        for event in model.alphabet():
            self.apply(model, state, event, False)

    def test_stale_success_and_error_cannot_acknowledge_another_active_job(self):
        model = AuditedLifecycle(jobs=2)
        delivered = self.prefix(model, (Event("claim"), complete(50), Event("receive", job_id=50), sink(50)))
        for active in (False, True):
            state = delivered
            if active:
                state = self.prefix(model, (Event("claim"), complete(50), Event("receive", job_id=50),
                    sink(50), Event("claim"), complete(51), Event("receive", job_id=51)))
            for event in (sink(50), sink(50, "stale error"), sink(999), sink(49, "unknown")):
                self.apply(model, state, event, False)
            self.assertEqual(state.core.delivering, 51 if active else None)
            self.assertEqual(state.core.report.delivered, 1)

    def test_unknown_and_inconsistent_completions_do_not_observe_stops(self):
        model = AuditedLifecycle(jobs=2, stop_on_failure=True)
        state = self.prefix(model, (Event("claim"),))
        for job_id in (0, 49, 51, 52, U64_MAX):
            for outcome in ("failure", "cancelled", "deadline"):
                self.apply(model, state, complete(job_id, outcome), False)
        for event in (complete(50, "factory_error", failure=True),
                      complete(50, "panicked", transitions=1),
                      complete(50, "model_error", cases=1),
                      complete(50, "factory_error", skipped_checks=1),
                      complete(50, "failure", failure=False),
                      complete(50, "completed", failure=True)):
            # Inconsistent summaries are syntactically valid codec inputs.
            self.assertEqual(model.decode_input(model.encode_input(event)), event)
            self.apply(model, state, event, False)

    def test_all_completion_stop_permutations_and_rejected_claim_controls(self):
        for order in permutations(("failure", "deadline", "cancelled")):
            with self.subTest(order=order):
                model = AuditedLifecycle(jobs=4, stop_on_failure=True)
                state = self.prefix(model, (Event("claim"),) * 3)
                for job_id, outcome in zip((50, 51, 52), order):
                    state = self.apply(model, state, complete(job_id, outcome))
                    self.apply(model, state, Event("claim", stop="cancelled"), False)
                for job_id in (52, 50, 51):
                    state = self.apply(model, state, Event("receive", job_id=job_id),
                                       effects=(Effect("deliver", job_id),))
                    state = self.apply(model, state, sink(job_id))
                state = self.apply(model, state, Event("finish"))
                self.assertEqual(state.core.report.termination, "cancelled")
                self.assertEqual((state.core.report.started, state.core.report.finished,
                                  state.core.report.not_started), (3, 3, 1))

    def test_sink_error_is_first_error_and_drain_counts_work(self):
        for worker_first in (False, True):
            model = AuditedLifecycle(jobs=3)
            state = self.prefix(model, (Event("claim"), Event("claim"), complete(50), complete(51, "cancelled"),
                                       Event("receive", job_id=50)))
            self.apply(model, state, Event("receive", job_id=51), False)
            worker = Event("worker_failed", error="worker", lost_work=False)
            if worker_first:
                state = self.apply(model, state, worker)
            state = self.apply(model, state, sink(50, "first sink"))
            if not worker_first:
                state = self.apply(model, state, worker)
            self.apply(model, state, sink(50, "second sink"), False)
            self.apply(model, state, Event("claim"), False)
            state = self.apply(model, state, Event("receive", job_id=51), effects=(Effect("discard", 51),))
            self.apply(model, state, sink(51), False)
            state = self.apply(model, state, Event("finish"))
            report = state.core.report
            self.assertEqual((report.termination, report.error), ("callback_error", "first sink"))
            self.assertEqual((report.started, report.finished, report.delivered, report.errors), (2, 2, 0, 0))
            self.assertTrue(report.accounting_complete)

    def test_worker_error_allows_delivery_and_requires_explicit_loss_to_abandon_work(self):
        model = AuditedLifecycle(jobs=2)
        state = self.prefix(model, (Event("claim"), Event("worker_failed", error="spawn")))
        self.apply(model, state, Event("finish"), False)
        state = self.apply(model, state, complete(50, "deadline"))
        state = self.apply(model, state, Event("receive", job_id=50), effects=(Effect("deliver", 50),))
        self.apply(model, state, Event("finish"), False)
        state = self.apply(model, state, sink(50))
        state = self.apply(model, state, Event("finish"))
        self.assertEqual((state.core.report.termination, state.core.report.error), ("worker_error", "spawn"))
        self.assertTrue(state.core.report.accounting_complete)
        missing = self.prefix(model, (Event("claim"), Event("worker_failed", error="spawn")))
        missing = self.apply(model, missing, Event("worker_failed", error="lost", lost_work=True))
        missing = self.apply(model, missing, Event("worker_failed", error="latest", lost_work=False))
        missing = self.apply(model, missing, Event("finish"))
        self.assertEqual(missing.core.report.error, "latest")
        self.assertEqual((missing.core.report.started, missing.core.report.finished), (1, 0))
        self.assertFalse(missing.core.report.accounting_complete)

    def test_job_errors_are_not_failure_stops_and_accounting_loss_is_sticky(self):
        for outcome, accounting in (("factory_error", True), ("model_error", False), ("panicked", False)):
            model = AuditedLifecycle(jobs=2, stop_on_failure=True)
            state = self.prefix(model, (Event("claim"), complete(50, outcome), Event("receive", job_id=50),
                sink(50), Event("claim"), complete(51), Event("receive", job_id=51), sink(51), Event("finish")))
            self.assertEqual((state.core.report.termination, state.core.report.errors,
                              state.core.report.failures), ("jobs_completed", 1, 0))
            self.assertEqual(state.core.report.accounting_complete, accounting)
        model = AuditedLifecycle(jobs=1, stop_on_failure=False)
        state = self.prefix(model, (Event("claim"), complete(50, "failure"), Event("receive", job_id=50),
                                   sink(50), Event("finish")))
        self.assertEqual((state.core.report.termination, state.core.report.failures), ("jobs_completed", 1))

    def test_each_aggregate_saturates_independently_at_u64_boundary(self):
        for counter in ("transitions", "cases", "skipped_checks"):
            model = AuditedLifecycle(jobs=2)
            state = self.prefix(model, (Event("claim"), complete(50, **{counter: U64_MAX}),
                                       Event("receive", job_id=50), sink(50)))
            self.assertTrue(state.core.report.accounting_complete)
            state = self.apply(model, state, Event("claim"), effects=(Effect("run_job", 51),))
            state = self.apply(model, state, complete(51, **{counter: 1}))
            state = self.apply(model, state, Event("receive", job_id=51), effects=(Effect("deliver", 51),))
            self.assertEqual(getattr(state.core.report, "reported_" + counter), U64_MAX)
            self.assertFalse(state.core.report.accounting_complete)
            state = self.apply(model, state, sink(51))
            self.apply(model, state, Event("finish"))

    def test_external_controls_are_observed_only_on_admitted_claim(self):
        for control in ("cancelled", "deadline"):
            model = AuditedLifecycle(jobs=1)
            state = self.prefix(model, (Event("claim", stop=control), Event("finish")))
            self.assertEqual((state.core.report.termination, state.core.report.started), (control, 0))
            state = self.prefix(model, (Event("claim"), complete(50), Event("receive", job_id=50), sink(50)))
            self.apply(model, state, Event("claim", stop=control), False)
            state = self.apply(model, state, Event("finish"))
            self.assertEqual(state.core.report.termination, "jobs_completed")

    def test_maximum_job_id_and_invalid_ranges(self):
        model = AuditedLifecycle(first_job=U64_MAX, jobs=1)
        state = self.prefix(model, (Event("claim"), complete(U64_MAX), Event("receive", job_id=U64_MAX),
                                   sink(U64_MAX), Event("finish")))
        self.assertEqual(state.ledger.claims, {U64_MAX})
        for first_job, jobs in ((50, 0), (U64_MAX, 2)):
            with self.assertRaisesRegex(ModelError, "job range"):
                Lifecycle(first_job, jobs, False).initial_state()

    def test_step_owned_and_snapshot_step_share_the_decision_law(self):
        model = AuditedLifecycle(jobs=2)
        state = model.core.initial_state()
        events = (Event("claim"), Event("claim"), complete(51, "factory_error"), complete(50),
                  Event("receive", job_id=51), sink(51), Event("receive", job_id=50), sink(50), Event("finish"))
        for event in events:
            before = deepcopy(state)
            snapshot = model.core.step(state, event)
            self.assertEqual(state, before)
            owned = model.core.step_owned(deepcopy(state), event)
            self.assertEqual(owned, snapshot)
            state = snapshot.state


class LifecycleCodecTests(_Assertions, unittest.TestCase):
    def test_manual_input_and_effect_bytes(self):
        codec = LifecycleCodec()
        id50 = bytes.fromhex("3200000000000000")
        rows = ((Event("claim"), b"\x00\x00"), (Event("claim", stop="cancelled"), b"\x00\x01"),
            (Event("claim", stop="deadline"), b"\x00\x02"), (Event("finish"), b"\x05"),
            (Event("receive", job_id=50), b"\x02" + id50),
            (sink(50), b"\x03" + id50 + b"\x00"),
            (sink(50, ""), b"\x03" + id50 + b"\x01" + b"\x00" * 8),
            (Event("worker_failed", error="x", lost_work=True), b"\x04\x01" + b"\x00" * 7 + b"x\x01"),
            (complete(50), b"\x01" + id50 + bytes.fromhex(
                "0000020000000000000001000000000000000100000000000000")))
        for event, expected in rows:
            with self.subTest(event=event):
                self.assertEqual(codec.encode_input(normalize_event(event)), expected)
                self.assertEqual(native_event(codec.decode_input(expected)), event)
        for tag, kind in enumerate(("run_job", "deliver", "discard")):
            expected = bytes([tag]) + id50
            self.assertEqual(codec.encode_output(normalize_effect(Effect(kind, 50))), expected)
            self.assertEqual(codec.decode_output(expected), normalize_effect(Effect(kind, 50)))

    def test_manual_initial_and_claimed_state_bytes(self):
        model = AuditedLifecycle(jobs=2)
        # Ten little-endian counts, accounting/termination/stop, pending count,
        # optional delivery, lost flag, finalized flag: exactly 94 initial bytes.
        expected = bytes.fromhex("0200000000000000") + b"\x00" * 16
        expected += bytes.fromhex("0200000000000000") + b"\x00" * 48
        expected += b"\x01\x00\x00" + b"\x00" * 11
        initial = normalize_state(model.core.initial_state())
        self.assertEqual(len(expected), 94)
        self.assertEqual(model.codec.encode_state(initial), expected)
        self.assertEqual(model.codec.decode_state(expected), initial)
        claimed = bytearray(expected)
        claimed[8:16] = (1).to_bytes(8, "little")
        claimed[24:32] = (1).to_bytes(8, "little")
        claimed[83:91] = (1).to_bytes(8, "little")
        claimed[91:91] = bytes.fromhex("320000000000000000")
        actual = model.core.step(model.core.initial_state(), Event("claim")).state
        self.assertEqual(model.codec.encode_state(normalize_state(actual)), bytes(claimed))

    def test_all_variants_roundtrip_with_strict_prefix_and_trailing_rejection(self):
        codec = LifecycleCodec()
        events = list(AuditedLifecycle().alphabet())
        events.extend((sink(50, "sink\0錯誤"), Event("worker_failed", error="worker\nerror"),
                       Event("receive", job_id=U64_MAX)))
        for event in events:
            encoded = codec.encode_input(normalize_event(event))
            self.assertEqual(native_event(codec.decode_input(encoded)), event)
            for end in range(len(encoded)):
                with self.assertRaises(ModelError):
                    codec.decode_input(encoded[:end])
            with self.assertRaises(ModelError):
                codec.decode_input(encoded + b"\x00")
        model = AuditedLifecycle(jobs=2)
        for outcome in OUTCOMES:
            state = self.prefix(model, (Event("claim"), complete(50, NATIVE[outcome])))
            value = normalize_state(state.core)
            encoded = codec.encode_state(value)
            self.assertEqual(codec.decode_state(encoded), value)
            for end in range(len(encoded)):
                with self.assertRaises(ModelError):
                    codec.decode_state(encoded[:end])
            with self.assertRaises(ModelError):
                codec.decode_state(encoded + b"\x00")

    def test_rejects_tags_flags_lengths_utf8_and_noncanonical_pending(self):
        codec = LifecycleCodec()
        for value in (b"\xff", b"\x00\x03", b"\x04" + b"\xff" * 8,
                      b"\x04\x01" + b"\x00" * 7 + b"\xff\x00",
                      b"\x04" + b"\x00" * 8 + b"\x02"):
            with self.assertRaises(ModelError):
                codec.decode_input(value)
        encoded = bytearray(codec.encode_input(normalize_event(complete(50))))
        for offset in (9, 10):
            bad = bytearray(encoded)
            bad[offset] = 255
            with self.assertRaises(ModelError):
                codec.decode_input(bytes(bad))
        initial = bytearray(codec.encode_state(normalize_state(Lifecycle(50, 2, False).initial_state())))
        for offset in (80, 81, 82, 91, 92, 93):
            bad = bytearray(initial)
            bad[offset] = 255
            with self.assertRaises(ModelError):
                codec.decode_state(bytes(bad))
        initial[83:91] = b"\xff" * 8
        with self.assertRaises(ModelError):
            codec.decode_state(bytes(initial))
        model = AuditedLifecycle(jobs=2)
        claimed = normalize_state(self.prefix(model, (Event("claim"), Event("claim"))).core)
        for pending in (list(reversed(claimed["pending"])), [claimed["pending"][0]] * 2):
            bad = dict(claimed, pending=pending)
            with self.assertRaises(ModelError):
                codec.decode_state(codec.encode_state(bad))

    def test_decode_checks_causal_properties_without_production_checker(self):
        codec = LifecycleCodec(jobs=2, stop_on_failure=True)
        initial = normalize_state(Lifecycle(50, 2, True).initial_state())
        bad_states = []
        callback = deepcopy(initial)
        callback["stop"] = "Sink"
        callback["report"].update(termination="CallbackError", error="never called")
        bad_states.append(callback)
        failure = deepcopy(initial)
        failure.update(stop="Failure", finalized=True)
        failure["report"]["termination"] = "FailureFound"
        bad_states.append(failure)
        lost = deepcopy(initial)
        lost.update(stop="Worker", lost_worker=True)
        lost["report"].update(termination="WorkerError", error="lost")
        bad_states.append(lost)
        for value in bad_states:
            with self.assertRaisesRegex(ModelError, "properties"):
                codec.decode_state(codec.encode_state(value))
        ready = normalize_state(self.prefix(AuditedLifecycle(jobs=2, stop_on_failure=True),
            (Event("claim"), complete(50, "failure"))).core)
        with self.assertRaises(ModelError):
            LifecycleCodec(jobs=2, stop_on_failure=False).decode_state(codec.encode_state(ready))
        with self.assertRaises(ModelError):
            LifecycleCodec(first_job=70, jobs=2, stop_on_failure=True).decode_state(codec.encode_state(ready))

    def test_state_roundtrips_delivery_all_terminations_and_lost_pending(self):
        model = AuditedLifecycle(jobs=2, stop_on_failure=True)
        cases = [
            (Event("claim"), complete(50), Event("receive", job_id=50)),
            (Event("claim"), complete(50, "failure"), Event("receive", job_id=50), sink(50), Event("finish")),
            (Event("claim", stop="cancelled"), Event("finish")),
            (Event("claim", stop="deadline"), Event("finish")),
            (Event("worker_failed", error=""), Event("finish")),
            (Event("claim"), Event("worker_failed", error="lost", lost_work=True), Event("finish")),
            (Event("claim"), complete(50), Event("receive", job_id=50), sink(50, "sink\0錯誤"), Event("finish")),
            (Event("claim"), complete(50), Event("receive", job_id=50), sink(50), Event("claim"),
             complete(51), Event("receive", job_id=51), sink(51), Event("finish")),
        ]
        for events in cases:
            state = self.prefix(model, events)
            value = normalize_state(state.core)
            encoded = model.codec.encode_state(value)
            self.assertEqual(model.codec.decode_state(encoded), value)
            self.assertEqual(model.codec.encode_state(model.codec.decode_state(encoded)), encoded)
            self.assertEqual(model.decode_state(model.encode_state(state)), state)
            for end in range(len(encoded)):
                with self.assertRaises(ModelError):
                    model.codec.decode_state(encoded[:end])
            with self.assertRaises(ModelError):
                model.codec.decode_state(encoded + b"\x00")


class LifecycleSelfModelTests(_Assertions, unittest.TestCase):
    def test_finite_one_job_graph_exhausts_and_fixed_seed_fuzz_uses_real_core(self):
        # This finite bound is explicit: one job, seven outcomes, both policies.
        # Multi-job permutations and missing outcomes have separate witnesses.
        for policy in (False, True):
            model = AuditedLifecycle(jobs=1, stop_on_failure=policy)
            graph = enumerate_states(model, SearchConfig(max_states=20_000, max_transitions=1_000_000, max_depth=20))
            self.assertIsNone(graph.failure)
            self.assertEqual(graph.termination, "graph_exhausted")
            self.assertEqual(graph.skipped_checks, 0)
            generated = fuzz(AuditedLifecycle(jobs=4, stop_on_failure=policy),
                FuzzConfig(seed=0x53544154454C4553, cases=64, max_steps=64, max_transitions=4096))
            self.assertIsNone(generated.failure)
            self.assertEqual(generated.termination, "cases_completed")
            self.assertEqual(generated.skipped_checks, 0)

    def test_incorrect_rejections_and_stale_ack_mutations_fail_independent_checks(self):
        model = AuditedLifecycle(jobs=2)
        prefixes = ((), (Event("claim"),), (Event("claim"), complete(50)),
                    (Event("claim"), complete(50), Event("receive", job_id=50)))
        events = (Event("claim"), complete(50), Event("receive", job_id=50), sink(50))
        for prefix, event in zip(prefixes, events):
            before = self.prefix(model, prefix)
            faulty = model.observe(before, event, Transition(deepcopy(before.core), (), Disposition("rejected", "fault")))
            failed = {check.id for check in model.check_transition(before, event, faulty) if check.status == "failed"}
            self.assertIn("campaign.admission", failed)
        before = self.prefix(model, (Event("claim"), complete(50), Event("receive", job_id=50), sink(50),
            Event("claim"), complete(51), Event("receive", job_id=51)))
        for error in (None, "stale error"):
            core = deepcopy(before.core)
            core.delivering = None
            if error is None:
                core.report.delivered += 1
            else:
                core.stop, core.report.termination, core.report.error = "sink", "callback_error", error
            event = sink(50, error)
            faulty = model.observe(before, event, Transition(core))
            failed = {check.id for check in model.check_state(faulty.state) if check.status == "failed"}
            self.assertTrue({"audit.protocol", "audit.active_sink"} <= failed)
            self.assertIn("campaign.admission", {check.id for check in model.check_transition(before, event, faulty)
                                                if check.status == "failed"})

    def test_forgotten_stop_is_detected_before_an_extra_claim(self):
        model = AuditedLifecycle(jobs=2, stop_on_failure=True)
        before = self.prefix(model, (Event("claim"),))
        event = complete(50, "failure")
        actual = model.core.step(before.core, event)
        actual.state.stop = None
        faulty = model.observe(before, event, actual)
        failed = {check.id for check in model.check_state(faulty.state) if check.status == "failed"}
        self.assertIn("audit.stop_observed", failed)
        extra = model.step(faulty.state, Event("claim"))
        self.assertIn("audit.protocol", {check.id for check in model.check_state(extra.state)
                                        if check.status == "failed"})

    def test_false_worker_loss_after_model_error_cannot_authorize_finish(self):
        model = AuditedLifecycle(jobs=2)
        before = self.prefix(model, (Event("claim"), Event("claim"), complete(50, "model_error"),
                                   Event("receive", job_id=50), sink(50)))
        self.assertFalse(before.core.report.accounting_complete)
        event = Event("worker_failed", error="startup", lost_work=False)
        actual = model.core.step(before.core, event)
        actual.state.lost_worker = True
        corrupted = model.observe(before, event, actual)
        self.assertIn("audit.loss_observed", {check.id for check in model.check_state(corrupted.state)
                                             if check.status == "failed"})
        self.assertFalse(corrupted.state.ledger.lost_work)
        # The corrupted core admits Finish, but the independent receipt ledger
        # rejects it: job51 has no completion/receipt and no genuine loss event.
        finished = model.step(corrupted.state, Event("finish"))
        self.assertEqual(finished.disposition.kind, "accepted")
        self.assertIn("campaign.admission", {check.id for check in
            model.check_transition(corrupted.state, Event("finish"), finished) if check.status == "failed"})

    def test_wrong_stop_identity_cannot_hide_behind_worker_termination(self):
        model = AuditedLifecycle(jobs=2)
        before = self.prefix(model, (Event("claim"), Event("worker_failed", error="worker")))
        event = complete(50, "cancelled")
        actual = model.core.step(before.core, event)
        self.assertEqual(actual.state.report.termination, "worker_error")
        self.assertEqual(actual.state.stop, "cancelled")
        actual.state.stop = "deadline"
        faulty = model.observe(before, event, actual)
        failed = {check.id for check in model.check_transition(before, event, faulty)
                  if check.status == "failed"}
        self.assertIn("campaign.stop_reason", failed)

    def test_duplicate_effect_is_found_fuzzed_shrunk_recorded_read_and_replayed(self):
        faulty = AuditedLifecycle(jobs=2, duplicate_delivery=True)
        explored = enumerate_states(faulty, SearchConfig(5000, 100_000, 10))
        self.assertEqual(explored.termination, "failure_found")
        self.assertIn("campaign.effects", {violation.check.id for violation in explored.failure.violations})
        self.assertIn("audit.protocol", {violation.check.id for violation in explored.failure.violations})
        generated = fuzz(faulty, FuzzConfig(seed=0x53544154454C4553, cases=128, max_steps=64))
        self.assertEqual(generated.termination, "failure_found")
        target = next(violation for violation in explored.failure.violations
                      if violation.phase == "transition" and violation.check.id == "campaign.effects")
        original = Failure((sink(999), Event("claim")) + explored.failure.inputs, (target,))
        minimized = shrink(faulty, original, ShrinkConfig(max_attempts=1000))
        self.assertTrue(minimized.validated_original)
        self.assertEqual(minimized.termination, "search_complete")
        self.assertEqual(len(minimized.minimized.inputs), 3)
        self.assertLess(len(minimized.minimized.inputs), len(original.inputs))
        self.assertTrue(any(violation.phase == "transition" and violation.check.id == "campaign.effects"
                            for violation in minimized.minimized.violations))
        for inputs in (original.inputs, minimized.minimized.inputs):
            trace = record(faulty, inputs)
            self.assertEqual(trace.termination, "property_failed")
            audit = AuditTrace.from_portable(trace, RunConfig("campaign-self-model-fault-control",
                parameters=(("injected_fault", "duplicate Deliver"),)))
            recovered = audit_from_bytes(audit_to_bytes(audit))
            self.assertEqual(recovered, audit)
            portable, config = recovered.to_portable()
            self.assertEqual(config, audit.config)
            replayed = replay(faulty, portable)
            self.assertEqual(replayed.outcome, "exact")
            self.assertTrue(replayed.failure_reproduced)
            healthy = AuditedLifecycle(jobs=2)
            strict = replay(healthy, portable)
            self.assertEqual(strict.outcome, "incompatible")
            self.assertFalse(strict.build_matches)
            relaxed = replay(healthy, portable, allow_build_mismatch=True)
            self.assertEqual(relaxed.outcome, "diverged")
            self.assertFalse(relaxed.failure_reproduced)

    def test_healthy_trace_roundtrips_and_replays_with_fresh_fixture(self):
        model = AuditedLifecycle(jobs=2)
        inputs = (Event("claim"), Event("claim"), complete(51, "factory_error"), complete(50, "failure"),
                  Event("receive", job_id=51), sink(51), Event("receive", job_id=50), sink(50), Event("finish"))
        trace = record(model, inputs)
        self.assertEqual(trace.termination, "completed")
        recovered = audit_from_bytes(audit_to_bytes(AuditTrace.from_portable(trace, RunConfig("campaign-healthy"))))
        replayed = replay(AuditedLifecycle(jobs=2), recovered.to_portable()[0])
        self.assertEqual((replayed.outcome, replayed.steps_verified, replayed.failure_reproduced), ("exact", 9, False))
        state = self.prefix(model, inputs)
        self.assertEqual(model.decode_state(model.encode_state(state)), state)

    def test_protocol_consumer_ignores_expected_steps_and_emits_exact_names(self):
        row = dict(schema="native-campaign-v1", case="manual-cancel", kind="lifecycle",
            config=dict(first_job="50", jobs="2", stop_on_failure=True),
            events=[{"Claim": {"stop": "Cancelled"}}, {"Finish": {}}], steps=["not an oracle"])
        actual = consume_lifecycle_case(row)
        self.assertEqual([step["accepted"] for step in actual["steps"]], [True, True])
        self.assertEqual(actual["steps"][-1]["state"]["report"]["termination"], "Cancelled")
        self.assertEqual(actual["steps"][-1]["state"]["report"]["not_started"], "2")
        self.assertEqual(actual["steps"][0]["input_hex"], "0001")
        self.assertEqual(actual["steps"][-1]["input_hex"], "05")
        self.assertEqual(actual["steps"][-1]["output_hex"], [])
        self.assertEqual(actual["initial"]["report"]["termination"], "JobsCompleted")
        self.assertEqual(row["steps"], ["not an oracle"])


if __name__ == "__main__":
    unittest.main()
