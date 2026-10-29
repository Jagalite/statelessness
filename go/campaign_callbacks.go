package statelessness

func campaignGuardModel[S, I, O any](g GenerateModel[S, I, O], p *campaignPanic) GenerateModel[S, I, O] {
	m := g
	if f := g.Inputs; f != nil {
		m.Inputs = func(s S) (Iterator[I], error) {
			defer p.guard()
			it, err := f(s)
			if err != nil {
				return nil, p.ownedError(err)
			}
			if it == nil {
				return nil, nil
			}
			return func() (I, bool, error) { defer p.guard(); v, ok, e := it(); return v, ok, p.ownedError(e) }, nil
		}
	}
	if f := g.InitialState; f != nil {
		m.InitialState = func() (S, error) { defer p.guard(); v, e := f(); return v, p.ownedError(e) }
	}
	if f := g.Step; f != nil {
		m.Step = func(s S, i I) (Transition[S, O], error) {
			defer p.guard()
			v, e := f(s, i)
			return v, p.ownedError(e)
		}
	}
	if f := g.CheckState; f != nil {
		m.CheckState = func(s S) ([]Check, error) { defer p.guard(); v, e := f(s); return v, p.ownedError(e) }
	}
	if f := g.CheckStateInto; f != nil {
		m.CheckStateInto = func(s S, out *CheckSink) error { defer p.guard(); return p.ownedError(f(s, out)) }
	}
	if f := g.CheckTransition; f != nil {
		m.CheckTransition = func(s S, i I, t Transition[S, O]) ([]Check, error) {
			defer p.guard()
			v, e := f(s, i, t)
			return v, p.ownedError(e)
		}
	}
	if f := g.CheckTransitionInto; f != nil {
		m.CheckTransitionInto = func(s S, i I, t Transition[S, O], out *CheckSink) error {
			defer p.guard()
			return p.ownedError(f(s, i, t, out))
		}
	}
	if f := g.CloneState; f != nil {
		m.CloneState = func(s S) S { defer p.guard(); return f(s) }
	}
	if f := g.CloneInput; f != nil {
		m.CloneInput = func(i I) I { defer p.guard(); return f(i) }
	}
	if f := g.CloneOutput; f != nil {
		m.CloneOutput = func(o O) O { defer p.guard(); return f(o) }
	}
	if f := g.EqualStates; f != nil {
		m.EqualStates = func(a, b S) bool { defer p.guard(); return f(a, b) }
	}
	if f := g.HashState; f != nil {
		m.HashState = func(s S) uint64 { defer p.guard(); return f(s) }
	}
	if f := g.Generate; f != nil {
		m.Generate = func(s S, rng *Rng) (I, bool, error) {
			defer p.guard()
			v, ok, e := f(s, rng)
			return v, ok, p.ownedError(e)
		}
	}
	if f := g.IsEnabled; f != nil {
		m.IsEnabled = func(s S, i I) (bool, error) { defer p.guard(); v, e := f(s, i); return v, p.ownedError(e) }
	}
	return m
}

func campaignGuardFeedback[S, I, O, K any](f Feedback[S, I, O, K], p *campaignPanic) Feedback[S, I, O, K] {
	g := f
	if fn := f.Metadata; fn != nil {
		g.Metadata = func() (FeedbackMetadata, error) { defer p.guard(); v, e := fn(); return v, p.ownedError(e) }
	}
	if fn := f.InitialFeatures; fn != nil {
		g.InitialFeatures = func(s S, out *FeatureBuffer[K]) error {
			defer p.guard()
			defer campaignExpireFeature(out)
			err := fn(s, out)
			campaignExpireFeature(out)
			return p.ownedError(err)
		}
	}
	if fn := f.TransitionFeatures; fn != nil {
		g.TransitionFeatures = func(s S, i I, t Transition[S, O], out *FeatureBuffer[K]) error {
			defer p.guard()
			defer campaignExpireFeature(out)
			err := fn(s, i, t, out)
			campaignExpireFeature(out)
			return p.ownedError(err)
		}
	}
	if fn := f.Key.Clone; fn != nil {
		g.Key.Clone = func(k K) K { defer p.guard(); return fn(k) }
	}
	if fn := f.Key.Equal; fn != nil {
		g.Key.Equal = func(a, b K) bool { defer p.guard(); return fn(a, b) }
	}
	if fn := f.Key.Hash; fn != nil {
		g.Key.Hash = func(k K) uint64 { defer p.guard(); return fn(k) }
	}
	return g
}

// Expire the shared lease (including copied FeatureBuffer handles) before any
// application Error method runs. The guided engine also revokes at its boundary.
func campaignExpireFeature[K any](out *FeatureBuffer[K]) {
	if out != nil && out.lease != nil {
		out.lease.active = false
		out.lease.values = nil
		out.lease.clone = nil
	}
}
