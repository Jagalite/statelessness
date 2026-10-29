"""Public guided-search contracts and explicit Python lifetime decisions.

Expected values here are hand-authored behavioral witnesses. The separate shared
guided-v2 consumer checks independently frozen cross-engine fixtures.
"""
from dataclasses import dataclass
import gc
import inspect
import unittest
from unittest.mock import patch
import weakref

from statelessness import (Check, Disposition, EXHAUSTED, Feedback, FeedbackMetadata,
    FeatureBuffer, FuzzConfig, GuidanceConfig, ModelError, Rng, RunLimits, StateFeedback,
    Transition, exact_state, fuzz, guided_fuzz, record, replay, shrink)


IDENTITY = FeedbackMetadata("test", 1, "hand-authored")


class Walk:
    def __init__(self): self.case, self.paths = 0, []
    def initial_state(self):
        self.case += 1
        self.paths.append([])
        return 0
    def generate(self, state, rng): return 1
    def is_enabled(self, state, input): return True
    def step(self, state, input):
        self.paths[-1].append(input)
        return Transition(state + input, (state + input,))
    def check_state(self, state): return ()


class EmptyFeedback(Feedback):
    def metadata(self): return IDENTITY
    def transition_features(self, before, input, after, out): pass


def run(model=None, feedback=None, *, cases=1, steps=3, guidance=None, **kwargs):
    return guided_fuzz(model or Walk(), feedback or exact_state(),
                       FuzzConfig(cases=cases, max_steps=steps, mutation_percent=0),
                       guidance or GuidanceConfig(), **kwargs)


@dataclass
class Collision:
    value: int
    def __hash__(self): return 0


class GuidedAccountingTests(unittest.TestCase):
    def test_defaults_and_ordinary_surface(self):
        self.assertEqual(GuidanceConfig(), GuidanceConfig(256, 25_600, 100_000, 256))
        self.assertEqual(FuzzConfig(), FuzzConfig(0, 1000, 100, 100000, 50))
        self.assertIsNone(fuzz(Walk(), FuzzConfig(cases=0)).guidance)
        result = run()
        self.assertEqual((result.termination, result.cases, result.transitions),
                         ("cases_completed", 1, 3))
        g = result.guidance
        self.assertEqual(g.algorithm, "feature-corpus-v2")
        self.assertEqual(g.feedback, FeedbackMetadata("exact-state", 1, "clone-eq-hash-v1"))
        self.assertEqual((g.unique_features, g.corpus_inputs), (4, 6))
        self.assertEqual([e.inputs for e in g.corpus], [(1,), (1, 1), (1, 1, 1)])
        self.assertEqual([(e.new_features, e.discovered_case, e.discovered_transition)
                          for e in g.corpus], [(1, 1, 1), (1, 1, 2), (1, 1, 3)])

    def test_default_initial_feedback_is_empty_for_protocol_and_duck_type(self):
        class Duck:
            def metadata(self): return IDENTITY
            def transition_features(self, before, input, after, out): out.push(after.state)
        self.assertEqual(run(feedback=EmptyFeedback()).guidance.unique_features, 0)
        self.assertEqual(run(feedback=Duck()).guidance.unique_features, 3)

    def test_hash_collisions_and_duplicate_pushes_do_not_merge_keys(self):
        class Keys(EmptyFeedback):
            def transition_features(self, before, input, after, out):
                out.push(Collision(after.state))
                out.push(Collision(after.state))
        g = run(feedback=Keys()).guidance
        self.assertEqual((g.unique_features, len(g.corpus)), (3, 3))
        self.assertEqual([e.new_features for e in g.corpus], [1, 1, 1])

    def test_fifo_eviction_by_entries_and_total_input_count(self):
        g = run(steps=4, guidance=GuidanceConfig(max_corpus_entries=2,
                                               max_corpus_inputs=6)).guidance
        # Admission at length3 evicts [1]; length4 must evict both [1,1]
        # and [1,1,1], not retain the shortest or most recent score.
        self.assertEqual([e.inputs for e in g.corpus], [(1, 1, 1, 1)])
        self.assertEqual((g.corpus_inputs, g.evicted_prefixes, g.dropped_prefixes), (4, 3, 0))
        self.assertTrue(g.corpus_limit_reached)
        self.assertFalse(g.feature_limit_reached)

    def test_exactly_filling_limits_does_not_set_saturation(self):
        g = run(steps=1, guidance=GuidanceConfig(1, 1, 2, 1)).guidance
        self.assertEqual((g.unique_features, g.corpus_inputs), (2, 1))
        self.assertFalse(g.feature_limit_reached or g.corpus_limit_reached)
        capped = run(steps=2, guidance=GuidanceConfig(1, 1, 2, 1)).guidance
        self.assertTrue(capped.feature_limit_reached)
        self.assertFalse(capped.corpus_limit_reached)

    def test_overlong_drop_once_then_recover_at_feature_cap(self):
        class Shortcut(Walk):
            def generate(self, state, rng): return 1 if self.case == 1 else 2
        class Target(EmptyFeedback):
            def transition_features(self, before, input, after, out):
                if after.state >= 2:
                    out.push("target")
                    out.push("target")
        g = run(Shortcut(), Target(), cases=2, steps=3,
                guidance=GuidanceConfig(max_corpus_inputs=1, max_features=1)).guidance
        self.assertEqual((g.unique_features, g.dropped_prefixes, g.evicted_prefixes), (1, 1, 0))
        self.assertTrue(g.corpus_limit_reached)
        self.assertFalse(g.feature_limit_reached)
        self.assertEqual([(e.inputs, e.new_features, e.discovered_case, e.discovered_transition)
                          for e in g.corpus], [((2,), 0, 2, 4)])

    def test_initial_recovery_marks_witnessed_without_admission(self):
        model = Walk()
        class Target(EmptyFeedback):
            def initial_features(self, state, out):
                if model.case > 1: out.push("target")
            def transition_features(self, before, input, after, out):
                if after.state >= 2 or model.case > 1: out.push("target")
        g = run(model, Target(), cases=2,
                guidance=GuidanceConfig(max_corpus_inputs=1)).guidance
        self.assertEqual((g.unique_features, g.dropped_prefixes, g.corpus), (1, 1, ()))

    def test_eviction_keeps_witnessed_bit(self):
        model = Walk()
        class Keys(EmptyFeedback):
            def transition_features(self, before, input, after, out):
                out.push(after.state if model.case == 1 else 1)
        g = run(model, Keys(), cases=2, steps=2,
                guidance=GuidanceConfig(max_corpus_entries=1)).guidance
        self.assertEqual((g.evicted_prefixes, len(g.corpus)), (1, 1))
        self.assertEqual((g.corpus[0].inputs, g.corpus[0].discovered_case), ((1, 1), 1))

    def test_rejected_and_ignored_turns_still_check_and_teach(self):
        for kind in ("rejected", "ignored"):
            class Rejected(Walk):
                def step(self, state, input):
                    return Transition(state + 1, (), Disposition(kind, "fixture"))
            g = run(Rejected()).guidance
            self.assertEqual((g.unique_features, len(g.corpus)), (4, 3))

    def test_exact_state_keeps_represented_history(self):
        class History(Walk):
            def initial_state(self): return (0, ())
            def step(self, state, input): return Transition((0, state[1] + (input,)))
        self.assertEqual(run(History()).guidance.unique_features, 4)
        coarse = StateFeedback(IDENTITY, lambda state: state[0])
        self.assertEqual(run(History(), coarse).guidance.unique_features, 1)
        self.assertEqual(run(History(), coarse).transitions, 3)


class GuidedOrderingTests(unittest.TestCase):
    def test_validation_before_clock_metadata_and_cancellation(self):
        for name in GuidanceConfig.__dataclass_fields__:
            for invalid in (0, -1, True, 1.5, 1 << 64):
                with self.subTest(name=name, invalid=invalid), self.assertRaises(ValueError):
                    GuidanceConfig(**{name: invalid})
        config = FuzzConfig()
        object.__setattr__(config, "mutation_percent", 101)
        guidance = GuidanceConfig()
        object.__setattr__(guidance, "max_features", 0)
        with self.assertRaisesRegex(ValueError, "max_features"):
            guided_fuzz(object(), object(), config, guidance,
                        limits=RunLimits(cancellation=lambda: self.fail("polled")))
        with self.assertRaisesRegex(ModelError, "mutation_percent"):
            guided_fuzz(object(), object(), config)

    def test_metadata_once_even_precancel_and_no_model_metadata(self):
        events = []
        class Meta(EmptyFeedback):
            def metadata(self): events.append("metadata"); return FeedbackMetadata("", 0, "")
        result = guided_fuzz(object(), Meta(), limits=RunLimits(cancellation=lambda: True))
        self.assertEqual(events, ["metadata"])
        self.assertEqual((result.termination, result.cases, result.transitions), ("cancelled", 0, 0))
        self.assertEqual(result.guidance.feedback, FeedbackMetadata("", 0, ""))

    def test_metadata_time_counts_against_deadline(self):
        clock = [10.0]
        class Slow(EmptyFeedback):
            def metadata(self): clock[0] = 12.0; return IDENTITY
        with patch("statelessness.search.time.monotonic", side_effect=lambda: clock[0]):
            result = guided_fuzz(object(), Slow(), limits=RunLimits(max_duration=1.0))
        self.assertEqual((result.termination, result.cases), ("deadline", 0))

    def test_property_precedes_feedback_and_cancellation(self):
        for failure_at in (0, 1):
            cancelled, events = [False], []
            class Failing(Walk):
                def check_state(self, state):
                    events.append(("check", state))
                    if state == failure_at:
                        cancelled[0] = True
                        return (Check("bad", "failed", "fixture"),)
                    return ()
                def check_transition(self, before, input, after):
                    events.append(("edge", after.state)); return ()
            class Obs:
                def initial(self, state, checks): events.append(("observe", state))
                def transition(self, before, input, after, sc, tc, history):
                    events.append(("observe", after.state))
            class F(EmptyFeedback):
                def transition_features(self, *args): self.fail()
                def fail(self): raise RuntimeError("feedback must not run")
            result = run(Failing(), F(), limits=RunLimits(cancellation=lambda: cancelled[0]), observer=Obs())
            self.assertEqual(result.termination, "failure_found")
            self.assertEqual(result.transitions, failure_at)
            self.assertEqual(events[-1], ("observe", failure_at))
            if failure_at: self.assertIn(("edge", 1), events)

    def test_transition_checker_error_wins_over_state_failure_with_partial_observation(self):
        batches = []
        class Broken(Walk):
            def check_state_into(self, state, out):
                out.push(Check("state", "failed", "fixture") if state else Check("initial"))
            def check_transition_into(self, before, input, after, out):
                out.push(Check("partial"))
                raise RuntimeError("checker owns error")
        class Observer:
            def check_error(self, phase, checks, complete): batches.append((phase, checks, complete))
            def transition(self, *args): raise AssertionError("not a checked edge")
        with self.assertRaisesRegex(ModelError, "transition check: checker owns error"):
            run(Broken(), observer=Observer())
        self.assertEqual(batches, [("state", (Check("state", "failed", "fixture"),), True),
                                  ("transition", (Check("partial"),), False)])

    def test_initial_checker_error_partial_batch_and_secondary_observer_error(self):
        seen = []
        class Broken(Walk):
            def check_state_into(self, state, out): out.push(Check("partial")); raise RuntimeError("check")
        class Observer:
            def check_error(self, phase, checks, complete):
                seen.append((phase, checks, complete)); raise RuntimeError("observer")
        with self.assertRaisesRegex(ModelError, "initial check: check") as caught:
            run(Broken(), observer=Observer())
        self.assertEqual(seen, [("initial_state", (Check("partial"),), False)])
        self.assertEqual(len(caught.exception.observer_errors), 1)

    def test_successful_feedback_learns_before_cancellation_but_error_wins(self):
        for fails in (False, True):
            cancelled = [False]
            class Cancel(EmptyFeedback):
                def transition_features(self, before, input, after, out):
                    out.push(after.state)
                    cancelled[0] = True
                    if fails: raise RuntimeError("feedback error wins")
            if fails:
                with self.assertRaisesRegex(ModelError, "transition feedback: feedback error wins"):
                    run(feedback=Cancel(), limits=RunLimits(cancellation=lambda: cancelled[0]))
            else:
                result = run(feedback=Cancel(), limits=RunLimits(cancellation=lambda: cancelled[0]))
                self.assertEqual((result.termination, result.transitions), ("cancelled", 1))
                self.assertEqual(result.guidance.corpus[0].inputs, (1,))

    def test_generation_error_precedes_cancel_and_exhaustion_is_polled(self):
        for fails in (False, True):
            cancelled = [False]
            class Stop(Walk):
                def generate(self, state, rng):
                    cancelled[0] = True
                    if fails: raise RuntimeError("generation error")
                    return EXHAUSTED
            if fails:
                with self.assertRaisesRegex(ModelError, "generate: generation error"):
                    run(Stop(), limits=RunLimits(cancellation=lambda: cancelled[0]))
            else:
                self.assertEqual(run(Stop(), limits=RunLimits(cancellation=lambda: cancelled[0])).termination,
                                 "cancelled")

    def test_budget_boundaries_and_candidate_before_next_case_cap(self):
        g = guided_fuzz(Walk(), exact_state(), FuzzConfig(cases=3, max_steps=0, max_transitions=0))
        self.assertEqual((g.termination, g.cases, g.guidance.unique_features), ("cases_completed", 3, 1))
        g = guided_fuzz(Walk(), exact_state(), FuzzConfig(cases=1, max_steps=1, max_transitions=1))
        self.assertEqual(g.termination, "cases_completed")
        g = guided_fuzz(Walk(), exact_state(), FuzzConfig(cases=2, max_steps=1,
                                                       max_transitions=1, mutation_percent=100))
        self.assertEqual((g.termination, g.cases, g.transitions, g.guidance.mutation_cases),
                         ("transition_limit", 2, 1, 1))
        g = guided_fuzz(object(), EmptyFeedback(), FuzzConfig(cases=0),
                        limits=RunLimits(cancellation=lambda: True))
        self.assertEqual(g.termination, "cancelled")

    def test_ordinary_control_polls_preserved_and_guided_adds_feedback_boundaries(self):
        for guided, expected in ((False, 8), (True, 10)):
            polls = []
            limits = RunLimits(cancellation=lambda: polls.append(None) is None and False)
            config = FuzzConfig(cases=1, max_steps=1)
            if guided: guided_fuzz(Walk(), EmptyFeedback(), config, limits=limits)
            else: fuzz(Walk(), config, limits=limits)
            self.assertEqual(len(polls), expected)

    def test_skipped_check_counter_retains_shared_u64_saturation(self):
        # Boundary injection represents complete checker batches without
        # allocating 2**64 checks; the public report must saturate both additions.
        with patch("statelessness.search._violations", return_value=((), (1 << 64) - 1)):
            report = run(steps=2)
        self.assertEqual(report.skipped_checks, (1 << 64) - 1)

    def test_observer_error_and_throwable_precede_feedback_and_property_return(self):
        for fault in (RuntimeError("observer stopped"), KeyboardInterrupt("observer interrupted")):
            class Failing(Walk):
                def check_state(self, state):
                    return (Check("bad", "failed", "fixture"),) if state else ()
            class Observer:
                def transition(self, *args): raise fault
            expected = KeyboardInterrupt if isinstance(fault, KeyboardInterrupt) else ModelError
            with self.assertRaisesRegex(expected, "observer"):
                run(Failing(), observer=Observer())


class GuidedOwnershipTests(unittest.TestCase):
    def test_emitted_mutable_key_snapshot_is_independent(self):
        shared = Collision(0)
        class Keys(EmptyFeedback):
            def transition_features(self, before, input, after, out):
                shared.value = after.state
                out.push(shared)
                shared.value = -1
        g = run(feedback=Keys(), cases=2).guidance
        self.assertEqual((g.unique_features, len(g.corpus)), (3, 3))

    def test_inputs_states_feedback_observer_and_corpus_are_detached(self):
        emitted, observed = [], []
        class Mutable(Walk):
            def initial_state(self): return []
            def generate(self, state, rng):
                state.append("bad")
                value = [len(emitted) + 1]; emitted.append(value); return value
            def is_enabled(self, state, input): input.append("bad"); return True
            def step(self, state, input):
                state.extend(input)
                input.append("bad")
                return Transition(state, ([state[-1]],))
        class Keys(EmptyFeedback):
            def transition_features(self, before, input, after, out):
                out.push(tuple(after.state))
                before.clear(); input.clear(); after.state.clear(); after.outputs[0].clear()
        class Observer:
            def transition(self, before, input, after, sc, tc, history):
                observed.append(history)
                input.clear(); after.state.clear()
                for value in history: value.append("observer")
        g = run(Mutable(), Keys(), observer=Observer()).guidance
        for value in emitted: value.clear()
        self.assertEqual([e.inputs for e in g.corpus], [([1],), ([1], [2]), ([1], [2], [3])])
        g.corpus[0].inputs[0].clear()
        self.assertEqual(g.corpus[1].inputs[0], [1])

    def test_retained_sinks_expire_on_every_exit_and_release_snapshots(self):
        class Fatal(BaseException): pass
        for fault in (None, RuntimeError("ordinary"), Fatal("fatal")):
            retained, refs = [], []
            class Payload(Collision):
                def __deepcopy__(self, memo):
                    value = Payload(self.value); refs.append(weakref.ref(value)); return value
            class Keys(EmptyFeedback):
                def transition_features(self, before, input, after, out):
                    retained.append(out); out.push(Payload(after.state))
                    if fault: raise fault
            if fault is None: run(feedback=Keys())
            else:
                with self.assertRaises(type(fault) if isinstance(fault, Fatal) else ModelError):
                    run(feedback=Keys())
            gc.collect()
            self.assertTrue(all(ref() is None for ref in refs))
            for sink in retained:
                with self.assertRaisesRegex(ModelError, "expired"): sink.push(1)

    def test_old_sink_cannot_write_into_later_callback(self):
        retained = []
        class Keys(EmptyFeedback):
            def transition_features(self, before, input, after, out):
                if retained:
                    with self_outer.assertRaisesRegex(ModelError, "expired"): retained[0].push("bad")
                retained.append(out); out.push(after.state)
        self_outer = self
        self.assertEqual(run(feedback=Keys()).guidance.unique_features, 3)

    def test_sink_already_revoked_when_key_equality_runs_after_callback(self):
        escaped, errors = [], []
        class Key:
            def __hash__(self): return 0
            def __eq__(self, other):
                try: escaped[-1].push("late key")
                except ModelError as error: errors.append(str(error))
                return isinstance(other, Key)
        class Keys(EmptyFeedback):
            def transition_features(self, before, input, after, out):
                escaped.append(out)
                out.push(Key())
        self.assertEqual(run(feedback=Keys()).guidance.unique_features, 1)
        self.assertTrue(errors)
        self.assertTrue(all("expired" in error for error in errors))

    def test_feature_overrun_counts_duplicates_and_callback_error_is_primary(self):
        for fault in (None, RuntimeError("own error"), KeyboardInterrupt("own interrupt")):
            retained = []
            class Spam(EmptyFeedback):
                def transition_features(self, before, input, after, out):
                    retained.append(out)
                    out.push(1)
                    try: out.push(1)
                    except ModelError: pass
                    if fault: raise fault
            expected = KeyboardInterrupt if isinstance(fault, KeyboardInterrupt) else ModelError
            with self.assertRaisesRegex(expected, "own" if fault else "per-observation"):
                run(feedback=Spam(), guidance=GuidanceConfig(max_features_per_observation=1))
            with self.assertRaisesRegex(ModelError, "expired"): retained[0].push(2)

    def test_overrun_checks_before_copy_and_failed_key_write_is_sticky(self):
        class NoCopy:
            def __deepcopy__(self, memo): raise AssertionError("should not clone excess key")
        out = FeatureBuffer(1); out.push(1)
        with self.assertRaisesRegex(ModelError, "per-observation"): out.push(NoCopy())
        class BadKey(EmptyFeedback):
            def transition_features(self, before, input, after, out):
                try: out.push([])
                except ModelError: pass
        with self.assertRaisesRegex(ModelError, "feature hash"):
            run(feedback=BadKey())

    def test_async_and_generator_feedback_returns_rejected_and_closed(self):
        results, sinks = [], []
        async def deferred(out): out.push("must not execute")
        def lazy(out):
            out.push("must not execute")
            yield None
        for factory in (deferred, lazy):
            class Invalid(EmptyFeedback):
                def transition_features(self, before, input, after, out):
                    sinks.append(out); value = factory(out); results.append(value); return value
            with self.assertRaisesRegex(ModelError, "return None synchronously"):
                run(feedback=Invalid())
        self.assertEqual(inspect.getcoroutinestate(results[0]), inspect.CORO_CLOSED)
        self.assertEqual(inspect.getgeneratorstate(results[1]), inspect.GEN_CLOSED)
        for out in sinks:
            with self.assertRaisesRegex(ModelError, "expired"): out.push(1)

    def test_cleanup_failure_is_secondary_and_sink_revoked_before_cleanup(self):
        cleanup = []
        class Invalid(EmptyFeedback):
            def transition_features(self, before, input, after, out):
                def lazy():
                    try: yield None
                    finally:
                        try: out.push("cleanup")
                        except ModelError as error: cleanup.append(str(error))
                        raise RuntimeError("cleanup failure")
                value = lazy(); next(value); return value
        with self.assertRaisesRegex(ModelError, "return None synchronously") as caught:
            run(feedback=Invalid())
        self.assertIn("expired", cleanup[0])
        self.assertIsInstance(caught.exception.__cause__.cleanup_error, RuntimeError)

    def test_async_metadata_projection_and_non_none_returns_rejected(self):
        async def metadata(): return IDENTITY
        class AsyncMeta(EmptyFeedback):
            def metadata(self): return metadata()
        with self.assertRaisesRegex(ModelError, "metadata.*synchronously"):
            run(feedback=AsyncMeta())
        async def project(state): return state
        with self.assertRaisesRegex(ModelError, "projection.*synchronously"):
            run(feedback=StateFeedback(IDENTITY, project))
        for value in (False, 1, (), object()):
            class Invalid(EmptyFeedback):
                def initial_features(self, state, out): return value
            with self.assertRaisesRegex(ModelError, "initial feedback.*synchronously"):
                run(feedback=Invalid())

    def test_custom_awaitables_and_async_generators_never_execute(self):
        executed = []
        class Awaitable:
            def __await__(self):
                executed.append("awaited")
                yield None
        async def lazy():
            executed.append("async generator")
            yield 1
        for factory in (Awaitable, lazy):
            for boundary in ("hook", "metadata", "projection"):
                with self.subTest(boundary=boundary, factory=factory):
                    class Invalid(EmptyFeedback):
                        def metadata(self): return factory() if boundary == "metadata" else IDENTITY
                        def initial_features(self, state, out): return factory()
                    feedback = StateFeedback(IDENTITY, lambda state: factory()) if boundary == "projection" else Invalid()
                    with self.assertRaisesRegex(ModelError, "synchronously"):
                        run(feedback=feedback)
        self.assertEqual(executed, [])

    def test_retained_bound_push_revoked_after_snapshot_hash_and_callback_throwables(self):
        class BadCopy:
            def __deepcopy__(self, memo): raise SystemExit("snapshot stopped")
        class BadHash:
            def __hash__(self): raise KeyboardInterrupt("hash stopped")
        for failure in (None, SystemExit("callback stopped"), BadCopy(), BadHash()):
            pushes = []
            class F(EmptyFeedback):
                def initial_features(self, state, out):
                    pushes.append(out.push)
                    if isinstance(failure, BaseException): raise failure
                    out.push(1 if failure is None else failure)
            if failure is None: run(feedback=F(), steps=0)
            else:
                with self.assertRaises(BaseException): run(feedback=F(), steps=0)
            with self.assertRaisesRegex(ModelError, "expired"): pushes[0]("escaped")

    def test_exception_formatting_cannot_replace_primary_callback_error(self):
        class BadMessage(Exception):
            def __str__(self): raise KeyboardInterrupt("diagnostic stopped")
        original, pushes = BadMessage(), []
        class F(EmptyFeedback):
            def initial_features(self, state, out):
                pushes.append(out.push)
                out.push(1)
                try: out.push(2)
                except ModelError: pass
                raise original
        with self.assertRaisesRegex(ModelError, "initial feedback: callback message unavailable") as caught:
            run(feedback=F(), guidance=GuidanceConfig(max_features_per_observation=1))
        self.assertIs(caught.exception.__cause__, original)
        self.assertIsInstance(caught.exception.diagnostic_error, KeyboardInterrupt)
        with self.assertRaisesRegex(ModelError, "expired"): pushes[0](3)

    def test_snapshot_hash_and_equality_errors_are_contextual_and_revoke_leases(self):
        class BadMessage(Exception):
            def __str__(self): raise ValueError("bad diagnostic")
        original = BadMessage()
        class BadCopy:
            def __deepcopy__(self, memo): raise original
        class BadHash:
            def __hash__(self): raise original
        class BadEquality:
            def __hash__(self): return 0
            def __eq__(self, other): raise original
        for factory in (BadCopy, BadHash, BadEquality):
            with self.subTest(factory=factory):
                pushes = []
                class F(EmptyFeedback):
                    def transition_features(self, before, input, after, out):
                        pushes.append(out.push)
                        out.push(factory())
                with self.assertRaisesRegex(ModelError, "transition feedback.*callback message unavailable") as caught:
                    run(feedback=F())
                current = caught.exception
                while current.__cause__ is not None: current = current.__cause__
                self.assertIs(current, original)
                for push in pushes:
                    with self.assertRaisesRegex(ModelError, "expired"): push(1)


class GuidedReentrancyTests(unittest.TestCase):
    def test_copy_and_hash_reentrancy_cannot_commit_or_clear_sticky_failure(self):
        for boundary in ("copy", "hash"):
            for fault in (None, RuntimeError("callback wins"), KeyboardInterrupt("callback wins")):
                with self.subTest(boundary=boundary, fault=type(fault)):
                    pushes, failures = [], []
                    class Key:
                        def nested(self):
                            try: pushes[-1]("nested")
                            except ModelError as error: failures.append(error)
                        def __deepcopy__(self, memo):
                            if boundary == "copy": self.nested()
                            return self
                        def __hash__(self):
                            if boundary == "hash": self.nested()
                            return 0
                    class F(EmptyFeedback):
                        def transition_features(self, before, input, after, out):
                            pushes.append(out.push)
                            try: out.push(Key())
                            except ModelError as error: failures.append(error)
                            try: out.push("later")
                            except ModelError as error: failures.append(error)
                            if fault is not None: raise fault
                    expected = KeyboardInterrupt if isinstance(fault, KeyboardInterrupt) else ModelError
                    with patch("statelessness.guided._GuidedDriver._learn") as learn:
                        with self.assertRaisesRegex(expected, "callback wins" if fault else "reentrant") as caught:
                            run(feedback=F(), guidance=GuidanceConfig(max_features_per_observation=1))
                        # The default-empty initial observation may learn; the
                        # failed transition must never reach admission.
                        self.assertEqual(learn.call_count, 1)
                    self.assertEqual(len(failures), 3)
                    self.assertTrue(all(error is failures[0] for error in failures))
                    if isinstance(fault, Exception): self.assertIs(caught.exception.__cause__, fault)
                    if isinstance(fault, KeyboardInterrupt): self.assertIs(caught.exception, fault)
                    with self.assertRaisesRegex(ModelError, "expired"): pushes[0]("escaped")

    def test_copy_and_hash_error_formatting_cannot_reenter_push(self):
        for boundary in ("copy", "hash"):
            pushes, diagnostics = [], []
            class BadMessage(Exception):
                def __str__(self):
                    try: pushes[-1]("diagnostic")
                    except ModelError as error: diagnostics.append(str(error))
                    return "original key error"
            original = BadMessage()
            class Key:
                def __deepcopy__(self, memo):
                    if boundary == "copy": raise original
                    return self
                def __hash__(self): raise original
            class F(EmptyFeedback):
                def transition_features(self, before, input, after, out):
                    pushes.append(out.push)
                    out.push(Key())
            with self.assertRaisesRegex(ModelError, "original key error") as caught:
                run(feedback=F())
            self.assertEqual(diagnostics, ["reentrant feature push is not supported"])
            self.assertIs(caught.exception.__cause__.__cause__, original)
            with self.assertRaisesRegex(ModelError, "expired"): pushes[0]("escaped")

    def test_callback_error_sink_is_revoked_before_hostile_formatting(self):
        pushes, diagnostics = [], []
        class BadMessage(Exception):
            def __str__(self):
                try: pushes[-1]("diagnostic")
                except ModelError as error: diagnostics.append(str(error))
                raise SystemExit("formatting stopped")
        original = BadMessage()
        class F(EmptyFeedback):
            def transition_features(self, before, input, after, out):
                pushes.append(out.push)
                raise original
        with self.assertRaisesRegex(ModelError, "callback message unavailable") as caught:
            run(feedback=F())
        self.assertEqual(diagnostics, ["feature buffer lease has expired"])
        self.assertIs(caught.exception.__cause__, original)
        self.assertIsInstance(caught.exception.diagnostic_error, SystemExit)
        with self.assertRaisesRegex(ModelError, "expired"): pushes[0]("escaped")

    def test_guided_configs_are_captured_before_metadata_without_deepcopy(self):
        class Fuzz(FuzzConfig):
            def __deepcopy__(self, memo): raise AssertionError("configuration copy hook")
        class Guidance(GuidanceConfig):
            def __deepcopy__(self, memo): raise AssertionError("configuration copy hook")
        class Limits(RunLimits):
            def __deepcopy__(self, memo): raise AssertionError("configuration copy hook")
        config = Fuzz(cases=1, max_steps=2, mutation_percent=0)
        guidance = Guidance(max_features=9)
        limits = Limits(cancellation=lambda: False)
        class F(EmptyFeedback):
            def metadata(self):
                # Deliberately bypass frozen assignment to test isolation from
                # an alias; normal callers only use immutable configurations.
                object.__setattr__(config, "cases", 0)
                object.__setattr__(guidance, "max_features", 1)
                object.__setattr__(limits, "cancellation", lambda: True)
                return IDENTITY
            def transition_features(self, before, input, after, out): out.push(after.state)
        report = guided_fuzz(Walk(), F(), config, guidance, limits=limits)
        self.assertEqual((report.cases, report.transitions, report.termination), (1, 2, "cases_completed"))
        self.assertEqual(report.guidance.unique_features, 2)
        self.assertEqual(report.guidance.config.max_features, 9)
        self.assertIs(type(report.guidance.config), GuidanceConfig)
        self.assertIsNot(report.guidance.config, guidance)


class GuidedMutationTests(unittest.TestCase):
    def test_shared_rng_zero_one_and_rejection_sampling(self):
        rng = Rng(0)
        self.assertIsNone(rng.index(0))
        self.assertEqual(rng.state, 0)
        self.assertEqual(rng.index(1), 0)
        self.assertEqual(rng.state, 0x9E3779B97F4A7C15)
        upper = (1 << 63) + 1
        threshold = (1 << 63) - 1
        class RejectionRng(Rng):
            def __init__(self): self.values = iter((0, threshold - 1, threshold)); self.draws = 0
            def next_u64(self): self.draws += 1; return next(self.values)
        rng = RejectionRng()
        self.assertEqual(rng.index(upper), threshold)
        self.assertEqual(rng.draws, 3)

    def test_all_modes_call_graph_and_single_replacement_position(self):
        # Scripted index choices are independent of the implementation; they
        # assert every upper bound, including index(1), and consume all choices.
        for mode, choices, expected in (
            (0, [(100, 0), (1, 0), (3, 0)], [1, 2, 3]),
            (1, [(100, 0), (1, 0), (3, 1), (3, 1), (2, 0), (3, 2)], [1, 3, 12]),
            (2, [(100, 0), (1, 0), (3, 2), (3, 1)], [1, 11, 3]),
        ):
            with self.subTest(mode=mode):
                draws = iter(choices)
                test = self
                class ScriptedRng:
                    def __init__(self, seed): pass
                    def index(self, upper):
                        bound, value = next(draws)
                        test.assertEqual(upper, bound)
                        return value
                class Sequence(Walk):
                    def generate(self, state, rng):
                        return len(self.paths[-1]) + (1 if self.case == 1 else 10)
                model = Sequence()
                class Last(EmptyFeedback):
                    def transition_features(self, before, input, after, out):
                        if model.case == 1 and len(model.paths[-1]) == 3: out.push("path")
                with patch("statelessness.search.Rng", ScriptedRng):
                    result = guided_fuzz(model, Last(), FuzzConfig(cases=2, max_steps=3, mutation_percent=100))
                self.assertEqual(model.paths, [[1, 2, 3], expected])
                self.assertEqual(result.guidance.mutation_cases, 1)
                self.assertEqual(list(draws), [])

    def test_zero_percent_still_draws_if_corpus_exists_and_empty_never_draws(self):
        draws = []
        class ScriptedRng:
            def __init__(self, seed): pass
            def index(self, upper): draws.append(upper); return 0
        with patch("statelessness.search.Rng", ScriptedRng):
            run(cases=3, steps=1)
        self.assertEqual(draws, [100, 100])
        draws.clear()
        with patch("statelessness.search.Rng", ScriptedRng):
            run(feedback=EmptyFeedback(), cases=3, steps=1)
        self.assertEqual(draws, [])

    def test_disabled_reuse_falls_back_to_generation_and_reused_is_not_revalidated(self):
        calls = []
        class Model(Walk):
            def is_enabled(self, state, input):
                calls.append((self.case, state, input))
                return self.case == 1 or input == 2
            def generate(self, state, rng): return self.case
        model = Model()
        class ScriptedRng:
            def __init__(self, seed): pass
            def index(self, upper): return 0
        with patch("statelessness.search.Rng", ScriptedRng):
            guided_fuzz(model, exact_state(), FuzzConfig(cases=2, max_steps=1, mutation_percent=100))
        self.assertEqual(calls, [(1, 0, 1), (2, 0, 1), (2, 0, 2)])
        calls.clear()
        model = Model()
        model.generate = lambda state, rng: 2
        with patch("statelessness.search.Rng", ScriptedRng):
            guided_fuzz(model, exact_state(), FuzzConfig(cases=2, max_steps=1, mutation_percent=100))
        self.assertEqual(calls, [(1, 0, 2), (2, 0, 2)])


if __name__ == "__main__":
    unittest.main()
