package statelessness

// GuidanceAlgorithm identifies the corpus admission and mutation policy.
const GuidanceAlgorithm = "feature-corpus-v2"

type FeedbackMetadata struct {
	Name    string `json:"name"`
	Version uint32 `json:"version"`
	Build   string `json:"build"`
}

// FeatureKey defines owned feature identity independently of model state equality.
// Clone and Equal are required; nil Hash uses one equality bucket. Equal keys
// must hash equally. Hooks must be deterministic and Clone must detach mutable
// storage. Equal and Hash receive snapshots, never the engine's retained keys.
type FeatureKey[K any] struct {
	Clone func(K) K
	Equal func(K, K) bool
	Hash  func(K) uint64
}

// Feedback emits keys in stable order. Nil InitialFeatures means no initial
// feedback. Metadata and TransitionFeatures are required. All callbacks run
// synchronously on the caller; errors and recoverable panics are ModelErrors.
// Callback arguments are snapshots. Do not concurrently use or retain a buffer
// for later writes; its lease expires when the callback returns or panics.
type Feedback[S, I, O, K any] struct {
	Key                FeatureKey[K]
	Metadata           func() (FeedbackMetadata, error)
	InitialFeatures    func(S, *FeatureBuffer[K]) error
	TransitionFeatures func(S, I, Transition[S, O], *FeatureBuffer[K]) error
}

// StateFeedback projects both initial and successor states into feature keys.
// The projection sees detached state; Push snapshots its result immediately.
func StateFeedback[S, I, O, K any](metadata FeedbackMetadata, key FeatureKey[K], project func(S) (K, error)) Feedback[S, I, O, K] {
	emit := func(state S, out *FeatureBuffer[K]) error {
		if project == nil {
			return &ModelError{"state feedback requires projection"}
		}
		value, e := project(state)
		if e != nil {
			return e
		}
		return out.Push(value)
	}
	return Feedback[S, I, O, K]{Key: key,
		Metadata:           func() (FeedbackMetadata, error) { return metadata, nil },
		InitialFeatures:    emit,
		TransitionFeatures: func(_ S, _ I, after Transition[S, O], out *FeatureBuffer[K]) error { return emit(after.State, out) },
	}
}

// ExactStateFeedback keeps full cloned state, including represented oracle
// history. It uses the model's equality and optional hash; no codec is required.
func ExactStateFeedback[S, I, O any](model Model[S, I, O]) Feedback[S, I, O, S] {
	return StateFeedback[S, I, O](FeedbackMetadata{"exact-state", 1, "clone-eq-hash-v1"},
		FeatureKey[S]{Clone: model.CloneState, Equal: model.EqualStates, Hash: model.HashState},
		func(state S) (S, error) { return state, nil })
}

// FeatureBuffer is a bounded, append-only callback lease. Every emission counts,
// including duplicates. Overrun and snapshot failures stay sticky if ignored;
// the callback's own returned error takes precedence over a sticky failure.
// A zero, expired, or copied expired buffer rejects Push without touching storage.
// Count limits do not bound callback allocations, key bytes, or process RSS.
type FeatureBuffer[K any] struct{ lease *featureLease[K] }
type featureLease[K any] struct {
	values          *[]K
	clone           func(K) K
	maximum         uint64
	active, writing bool
	failure         error
}

func (b *FeatureBuffer[K]) Push(key K) error {
	if b == nil || b.lease == nil || !b.lease.active {
		return &ModelError{"feature buffer lease expired"}
	}
	l := b.lease
	if l.failure != nil {
		return l.failure
	}
	if l.writing {
		l.failure = &ModelError{"feature buffer reentrant push"}
		return l.failure
	}
	if uint64(len(*l.values)) >= l.maximum {
		l.failure = &ModelError{"feedback exceeds per-observation feature limit"}
		return l.failure
	}
	l.writing = true
	defer func() { l.writing = false }()
	value, e := copied(l.clone, key)
	if l.failure != nil {
		return l.failure
	}
	if e != nil {
		l.failure = e
		return e
	}
	if e := countCanAppend[K](len(*l.values)); e != nil {
		l.failure = e
		return e
	}
	*l.values = append(*l.values, value)
	return nil
}

// GuidanceConfig64 bounds independent retained counts, not bytes or host memory.
type GuidanceConfig64 struct {
	MaxCorpusEntries          uint64 `json:"max_corpus_entries"`
	MaxCorpusInputs           uint64 `json:"max_corpus_inputs"`
	MaxFeatures               uint64 `json:"max_features"`
	MaxFeaturesPerObservation uint64 `json:"max_features_per_observation"`
}

func DefaultGuidanceConfig64() GuidanceConfig64 { return GuidanceConfig64{256, 25600, 100000, 256} }
func (c GuidanceConfig64) Validate() error {
	if c.MaxCorpusEntries <= 0 || c.MaxCorpusInputs <= 0 || c.MaxFeatures <= 0 || c.MaxFeaturesPerObservation <= 0 {
		return &ConfigError{"guidance limits must be positive"}
	}
	return nil
}

type CorpusEntry64[I any] struct {
	Inputs []I `json:"inputs"`
	// NewFeatures is zero for recovery of previously dropped discoveries.
	NewFeatures          uint64 `json:"new_features"`
	DiscoveredCase       uint64 `json:"discovered_case"`
	DiscoveredTransition uint64 `json:"discovered_transition"`
}
type GuidanceReport64[I any] struct {
	Algorithm           string             `json:"algorithm"`
	Config              GuidanceConfig64   `json:"config"`
	Feedback            FeedbackMetadata   `json:"feedback"`
	UniqueFeatures      uint64             `json:"unique_features"`
	CorpusInputs        uint64             `json:"corpus_inputs"`
	Corpus              []CorpusEntry64[I] `json:"corpus"`
	MutationCases       uint64             `json:"mutation_cases"`
	DroppedPrefixes     uint64             `json:"dropped_prefixes"`
	EvictedPrefixes     uint64             `json:"evicted_prefixes"`
	FeatureLimitReached bool               `json:"feature_limit_reached"`
	CorpusLimitReached  bool               `json:"corpus_limit_reached"`
}

func GuidedFuzz64[S, I, O, K any](g GenerateModel[S, I, O], config FuzzConfig64, guidance GuidanceConfig64, feedback Feedback[S, I, O, K]) (*FuzzReport64[I], error) {
	return GuidedFuzzWithLimits64(g, config, guidance, feedback, RunLimits{})
}
func GuidedFuzzWithLimits64[S, I, O, K any](g GenerateModel[S, I, O], config FuzzConfig64, guidance GuidanceConfig64, feedback Feedback[S, I, O, K], limits RunLimits) (*FuzzReport64[I], error) {
	return guidedFuzzObserved64(g, config, guidance, feedback, limits, nil)
}

// GuidedFuzzObserved64 keeps observation independent of corpus selection. As with
// FuzzObserved, observer errors and panics abort rather than yielding a report.
func GuidedFuzzObserved64[S, I, O, K any](g GenerateModel[S, I, O], config FuzzConfig64, guidance GuidanceConfig64, feedback Feedback[S, I, O, K], limits RunLimits, observer SearchObserver[S, I, O]) (*FuzzReport64[I], error) {
	return guidedFuzzObserved64(g, config, guidance, feedback, limits, &observer)
}
func guidedFuzzObserved64[S, I, O, K any](g GenerateModel[S, I, O], config FuzzConfig64, guidance GuidanceConfig64, feedback Feedback[S, I, O, K], limits RunLimits, observer *SearchObserver[S, I, O]) (*FuzzReport64[I], error) {
	if e := guidance.Validate(); e != nil {
		return nil, e
	}
	if e := validateFuzzConfig64(config); e != nil {
		return nil, e
	}
	if e := validateFuzzModel(g); e != nil {
		return nil, e
	}
	if feedback.Metadata == nil || feedback.TransitionFeatures == nil || feedback.Key.Clone == nil || feedback.Key.Equal == nil {
		return nil, &ModelError{"feedback requires metadata, transition, clone and equality callbacks"}
	}
	control, e := newRunControl(limits)
	if e != nil {
		return nil, e
	}
	metadata, e := call("feedback metadata", feedback.Metadata)
	if e != nil {
		return nil, e
	}
	driver := &guidedDriver[S, I, O, K]{model: g, feedback: feedback, seen: make(map[uint64][]*seenFeature[K]),
		report: GuidanceReport64[I]{Algorithm: GuidanceAlgorithm, Config: guidance, Feedback: metadata, Corpus: []CorpusEntry64[I]{}},
	}
	report, e := fuzzKernel64(g, config, control, observer, driver)
	if e != nil {
		return nil, e
	}
	report.Guidance = &driver.report
	return report, nil
}

type seenFeature[K any] struct {
	key       K
	witnessed bool
}
type guidedDriver[S, I, O, K any] struct {
	model    GenerateModel[S, I, O]
	feedback Feedback[S, I, O, K]
	report   GuidanceReport64[I]
	seen     map[uint64][]*seenFeature[K]
	scratch  []K
}

func (d *guidedDriver[S, I, O, K]) observe(stage string, inputs []I, caseNumber uint64, transition uint64, callback func(*FeatureBuffer[K]) error) error {
	clear(d.scratch)
	d.scratch = d.scratch[:0]
	l := &featureLease[K]{values: &d.scratch, clone: d.feedback.Key.Clone, maximum: d.report.Config.MaxFeaturesPerObservation, active: true}
	revoke := func() { l.active = false; l.values = nil; l.clone = nil }
	defer func() { revoke(); clear(d.scratch); d.scratch = d.scratch[:0] }()
	_, e := call(stage, func() (struct{}, error) {
		// Revoke while leaving the callback, before call formats its error or
		// recovered panic. Formatting can itself invoke application methods.
		defer revoke()
		if e := callback(&FeatureBuffer[K]{lease: l}); e != nil {
			return struct{}{}, e
		}
		if l.failure != nil {
			return struct{}{}, l.failure
		}
		return struct{}{}, nil
	})
	// The lease is already revoked before equality/hash hooks begin.
	if e != nil {
		return e
	}
	_, e = call(stage, func() (struct{}, error) { return struct{}{}, d.learn(inputs, caseNumber, transition) })
	return e
}
func (d *guidedDriver[S, I, O, K]) initial(state S, caseNumber uint64) error {
	return d.observe("initial feedback", nil, caseNumber, 0, func(out *FeatureBuffer[K]) error {
		if d.feedback.InitialFeatures == nil {
			return nil
		}
		return d.feedback.InitialFeatures(d.model.CloneState(state), out)
	})
}
func (d *guidedDriver[S, I, O, K]) transition(before S, input I, after Transition[S, O], inputs []I, caseNumber uint64, transition uint64) error {
	return d.observe("transition feedback", inputs, caseNumber, transition, func(out *FeatureBuffer[K]) error {
		snapshot, e := cloneTransition(d.model.Model, after)
		if e != nil {
			return e
		}
		return d.feedback.TransitionFeatures(d.model.CloneState(before), d.model.CloneInput(input), snapshot, out)
	})
}
func (d *guidedDriver[S, I, O, K]) learn(inputs []I, caseNumber uint64, transition uint64) error {
	keyOps := d.feedback.Key
	novel, recovered := uint64(0), false
	retainable := uint64(len(inputs)) <= d.report.Config.MaxCorpusInputs
	for _, key := range d.scratch {
		hash := uint64(0)
		if keyOps.Hash != nil {
			hash = keyOps.Hash(keyOps.Clone(key))
		}
		var found *seenFeature[K]
		for _, prior := range d.seen[hash] {
			if keyOps.Equal(keyOps.Clone(prior.key), keyOps.Clone(key)) {
				found = prior
				break
			}
		}
		if found != nil {
			if retainable && !found.witnessed {
				found.witnessed = true
				recovered = true
			}
		} else if d.report.UniqueFeatures == d.report.Config.MaxFeatures {
			d.report.FeatureLimitReached = true
		} else {
			if _, exists := d.seen[hash]; !exists {
				if e := countCanAppend[struct {
					hash   uint64
					bucket []*seenFeature[K]
				}](len(d.seen)); e != nil {
					return e
				}
			}
			if e := countCanAppend[*seenFeature[K]](len(d.seen[hash])); e != nil {
				return e
			}
			d.seen[hash] = append(d.seen[hash], &seenFeature[K]{key: key, witnessed: retainable})
			d.report.UniqueFeatures++
			novel++
		}
	}
	if (novel == 0 && !recovered) || len(inputs) == 0 {
		return nil
	}
	if !retainable {
		d.report.CorpusLimitReached = true
		d.report.DroppedPrefixes++
		return nil
	}
	owned, e := cloneInputs(d.model, inputs)
	if e != nil {
		return e
	}
	allowance := d.report.Config.MaxCorpusInputs - uint64(len(inputs))
	for uint64(len(d.report.Corpus)) >= d.report.Config.MaxCorpusEntries || d.report.CorpusInputs > allowance {
		d.report.CorpusInputs -= uint64(len(d.report.Corpus[0].Inputs))
		d.report.Corpus[0] = CorpusEntry64[I]{}
		d.report.Corpus = d.report.Corpus[1:]
		d.report.EvictedPrefixes++
		d.report.CorpusLimitReached = true
	}
	d.report.CorpusInputs += uint64(len(inputs))
	if e := countCanAppend[CorpusEntry64[I]](len(d.report.Corpus)); e != nil {
		return e
	}
	d.report.Corpus = append(d.report.Corpus, CorpusEntry64[I]{owned, novel, caseNumber, transition})
	return nil
}
func (d *guidedDriver[S, I, O, K]) candidate(_ []I, rng *Rng, percent int) ([]I, int, error) {
	if len(d.report.Corpus) == 0 {
		return nil, -1, nil
	}
	draw, _ := rng.Index(100)
	if draw >= uint64(percent) {
		return nil, -1, nil
	}
	d.report.MutationCases++
	index, _ := rng.Index(uint64(len(d.report.Corpus)))
	candidate, e := cloneInputs(d.model, d.report.Corpus[index].Inputs)
	if e != nil {
		return nil, -1, e
	}
	mode, _ := rng.Index(3)
	switch mode {
	case 0:
		return candidate, -1, nil
	case 1:
		start, _ := rng.Index(uint64(len(candidate)))
		span, _ := rng.Index(uint64(len(candidate)) - start)
		end := int(start + span + 1)
		candidate = append(candidate[:int(start)], candidate[end:]...)
		position, _ := rng.Index(uint64(len(candidate)) + 1)
		return candidate, int(position), nil
	default:
		position, _ := rng.Index(uint64(len(candidate)))
		return candidate, int(position), nil
	}
}
