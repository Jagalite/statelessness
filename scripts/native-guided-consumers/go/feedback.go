package main

import (
	"encoding/binary"
	"errors"
	"hash/fnv"

	sl "github.com/Jagalite/statelessness/go"
)

func hashInts(values []int64) uint64 {
	h := fnv.New64a()
	var bytes [8]byte
	for _, value := range values {
		binary.LittleEndian.PutUint64(bytes[:], uint64(value))
		_, _ = h.Write(bytes[:])
	}
	return h.Sum64()
}

func featureHash(k feature) uint64 {
	h := fnv.New64a()
	_, _ = h.Write([]byte{k.kind})
	_, _ = h.Write([]byte(k.identity))
	var bytes [8]byte
	for _, value := range k.integers {
		binary.LittleEndian.PutUint64(bytes[:], uint64(value))
		_, _ = h.Write(bytes[:])
	}
	return h.Sum64()
}

func feedbackMetadata() sl.FeedbackMetadata {
	return sl.FeedbackMetadata{Name: "table-features", Version: 1, Build: "guided-v2-fixture-v1"}
}

// Wrap helpers transparently. Their private Push operations are deliberately
// not synthesized in the observable callback trace.
func traceFeedback[K any](f *fixture, inner sl.Feedback[state, int64, int64, K]) sl.Feedback[state, int64, int64, K] {
	wrapped := inner
	wrapped.Metadata = func() (sl.FeedbackMetadata, error) {
		f.enter("metadata")
		defer f.done("metadata")
		return inner.Metadata()
	}
	if inner.InitialFeatures != nil {
		wrapped.InitialFeatures = func(s state, out *sl.FeatureBuffer[K]) error {
			f.enter("initial_feedback", f.caseNumber, s)
			defer f.done("initial_feedback")
			return inner.InitialFeatures(s, out)
		}
	}
	wrapped.TransitionFeatures = func(before state, input int64, after transition, out *sl.FeatureBuffer[K]) error {
		f.enter("transition_feedback", f.caseNumber, before, input, after.State, nonnil(after.Outputs),
			normalizeDisposition(after.Disposition))
		defer f.done("transition_feedback")
		return inner.TransitionFeatures(before, input, after, out)
	}
	return wrapped
}

func (f *fixture) customFeedback() sl.Feedback[state, int64, int64, feature] {
	key := sl.FeatureKey[feature]{Clone: cloneFeature, Equal: equalFeature}
	if f.spec.Feedback.Hash == "value" {
		key.Hash = featureHash
	} else {
		// Exercise the public optional-hash single-bucket contract.
		key.Hash = nil
	}
	emit := func(keys []feature, ownError *string, out *sl.FeatureBuffer[feature]) error {
		for _, key := range keys {
			err := out.Push(cloneFeature(key))
			f.trace = append(f.trace, []any{"push", cloneFeature(key), err == nil})
			if err != nil && !f.spec.Feedback.IgnoreOverrun {
				return err
			}
		}
		if ownError != nil {
			return errors.New(*ownError)
		}
		return nil
	}
	feedback := sl.Feedback[state, int64, int64, feature]{
		Key:      key,
		Metadata: func() (sl.FeedbackMetadata, error) { return feedbackMetadata(), nil },
		TransitionFeatures: func(_ state, _ int64, after transition, out *sl.FeatureBuffer[feature]) error {
			for _, row := range f.spec.Feedback.Transitions {
				if row.matches(f.caseNumber, after.State[0]) {
					return emit(row.Keys, row.Error, out)
				}
			}
			return nil
		},
	}
	if f.spec.Feedback.Initial != nil {
		feedback.InitialFeatures = func(_ state, out *sl.FeatureBuffer[feature]) error {
			return emit(f.spec.Feedback.Initial, f.spec.Feedback.InitialError, out)
		}
	}
	return traceFeedback(f, feedback)
}

func (f *fixture) run(g generatedModel, config sl.FuzzConfig) (*sl.FuzzReport[int64], error) {
	if f.spec.Mode == "ordinary" {
		return sl.FuzzObserved(g, config, f.limits(), f.observer())
	}
	switch f.spec.Feedback.Kind {
	case "exact":
		feedback := traceFeedback(f, sl.ExactStateFeedback(g.Model))
		return sl.GuidedFuzzObserved(g, config, f.spec.Guidance, feedback, f.limits(), f.observer())
	case "state":
		key := sl.FeatureKey[int64]{Clone: sl.ValueCopy[int64], Equal: func(a, b int64) bool { return a == b }}
		if f.spec.Feedback.Hash == "value" {
			key.Hash = func(value int64) uint64 { return hashInts([]int64{value}) }
		}
		feedback := sl.StateFeedback[state, int64, int64](feedbackMetadata(), key,
			func(s state) (int64, error) { return s[f.spec.Feedback.Project], nil })
		return sl.GuidedFuzzObserved(g, config, f.spec.Guidance, traceFeedback(f, feedback), f.limits(), f.observer())
	default: // validated custom feedback
		return sl.GuidedFuzzObserved(g, config, f.spec.Guidance, f.customFeedback(), f.limits(), f.observer())
	}
}
