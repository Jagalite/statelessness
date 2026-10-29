package statelessness

import (
	"cmp"
	"fmt"
	"reflect"
	"slices"
	"strings"
)

// ScopedKey identifies an operation independently of logical time.
type ScopedKey[K cmp.Ordered] struct {
	Epoch, Generation uint64
	Key               K
}
type LifecycleEvent[K comparable] struct {
	Kind string
	Key  K
	Time uint64
}
type LifecycleObligation struct {
	Deadline                      uint64
	Cancelled, Settled, Published bool
}

func (o LifecycleObligation) pending() bool { return !o.Cancelled && !o.Settled }

type LifecycleViolation[K comparable] struct {
	Legacy      *string
	Key         *K
	Code, Event string
	ObservedAt  uint64
	Prior       *LifecycleObligation
}
type LifecycleEntry[K comparable] struct {
	Key        K
	Obligation LifecycleObligation
}
type LifecycleResource[K comparable] struct {
	Key      K
	Acquired bool
}
type LifecycleHistory[K comparable] struct {
	Now, RetiredBefore uint64
	Obligations        []LifecycleEntry[K]
	Resources          []LifecycleResource[K]
	Violations         []LifecycleViolation[K]
}
type RetentionLimits struct{ Obligations, Resources, Violations int }
type RetentionStatus[K comparable] struct {
	ActiveObligations, HistoricalObligations, AcquiredResources, HistoricalResources int
	OldestUnresolved                                                                 *K
	OldestDeadline, RetiredBefore                                                    uint64
}

func (h LifecycleHistory[K]) Retention() RetentionStatus[K] {
	r := RetentionStatus[K]{RetiredBefore: h.RetiredBefore}
	for _, e := range h.Obligations {
		if e.Obligation.pending() {
			r.ActiveObligations++
			if r.OldestUnresolved == nil || e.Obligation.Deadline < r.OldestDeadline {
				k := e.Key
				r.OldestUnresolved = &k
				r.OldestDeadline = e.Obligation.Deadline
			}
		} else {
			r.HistoricalObligations++
		}
	}
	for _, e := range h.Resources {
		if e.Acquired {
			r.AcquiredResources++
		} else {
			r.HistoricalResources++
		}
	}
	return r
}
func (h LifecycleHistory[K]) Clone() LifecycleHistory[K] {
	h.Obligations = slices.Clone(h.Obligations)
	h.Resources = slices.Clone(h.Resources)
	h.Violations = slices.Clone(h.Violations)
	for i, v := range h.Violations {
		if v.Legacy != nil {
			text := *v.Legacy
			h.Violations[i].Legacy = &text
		}
		if v.Key != nil {
			k := *v.Key
			h.Violations[i].Key = &k
		}
		if v.Prior != nil {
			p := *v.Prior
			h.Violations[i].Prior = &p
		}
	}
	return h
}

// LifecycleMonitor is pure: Advance returns an owned history or an incomplete
// error. Epoch and Compare are set together by NewIndexedLifecycleMonitor.
type LifecycleMonitor[K comparable] struct {
	Limits  RetentionLimits
	Epoch   func(K) uint64
	Compare func(K, K) int
}

func NewLifecycleMonitor[K comparable](max int) LifecycleMonitor[K] {
	return LifecycleMonitor[K]{Limits: RetentionLimits{max, max, max}}
}
func NewIndexedLifecycleMonitor[K cmp.Ordered](limits RetentionLimits) LifecycleMonitor[ScopedKey[K]] {
	return LifecycleMonitor[ScopedKey[K]]{Limits: limits, Epoch: func(k ScopedKey[K]) uint64 { return k.Epoch }, Compare: func(a, b ScopedKey[K]) int {
		if n := cmp.Compare(a.Epoch, b.Epoch); n != 0 {
			return n
		}
		if n := cmp.Compare(a.Generation, b.Generation); n != 0 {
			return n
		}
		return cmp.Compare(a.Key, b.Key)
	}}
}
func (m LifecycleMonitor[K]) Validate(h LifecycleHistory[K]) error {
	for _, e := range h.Obligations {
		if !immutableLifecycleKey(reflect.ValueOf(e.Key)) {
			return &ModelError{"lifecycle keys must be immutable reflexive values"}
		}
	}
	for _, e := range h.Resources {
		if !immutableLifecycleKey(reflect.ValueOf(e.Key)) {
			return &ModelError{"lifecycle keys must be immutable reflexive values"}
		}
	}
	l := m.Limits
	if l.Obligations < 0 || l.Resources < 0 || l.Violations < 0 || len(h.Obligations) > l.Obligations || len(h.Resources) > l.Resources || len(h.Violations) > l.Violations {
		return &ModelError{"lifecycle retention limit; verification incomplete"}
	}
	if m.Epoch != nil {
		if m.Compare == nil {
			return &ModelError{"indexed history requires ordering"}
		}
		for i, e := range h.Obligations {
			if m.Epoch(e.Key) < h.RetiredBefore || e.Obligation.Published && !e.Obligation.Settled || i > 0 && m.Compare(h.Obligations[i-1].Key, e.Key) >= 0 {
				return &ModelError{"invalid indexed obligation history"}
			}
		}
		for i, e := range h.Resources {
			if m.Epoch(e.Key) < h.RetiredBefore || i > 0 && m.Compare(h.Resources[i-1].Key, e.Key) >= 0 {
				return &ModelError{"invalid indexed resource history"}
			}
		}
	}
	return nil
}
func (m LifecycleMonitor[K]) Advance(before LifecycleHistory[K], events []LifecycleEvent[K]) (LifecycleHistory[K], error) {
	var empty LifecycleHistory[K]
	if e := m.Validate(before); e != nil {
		return empty, e
	}
	h := before.Clone()
	index := map[K]int{}
	resources := map[K]int{}
	for i, e := range h.Obligations {
		if _, ok := index[e.Key]; !ok {
			index[e.Key] = i
		}
	}
	for i, e := range h.Resources {
		if _, ok := resources[e.Key]; !ok {
			resources[e.Key] = i
		}
	}
	violation := func(k *K, code, event string) error {
		if len(h.Violations) >= m.Limits.Violations {
			return &ModelError{"lifecycle violation limit; verification incomplete"}
		}
		v := LifecycleViolation[K]{Code: code, Event: event, ObservedAt: h.Now}
		if m.Epoch == nil {
			text := legacyLifecycleText(code)
			v.Legacy = &text
		}
		if k != nil {
			key := *k
			v.Key = &key
			if n, ok := index[key]; ok {
				o := h.Obligations[n].Obligation
				v.Prior = &o
			}
		}
		h.Violations = append(h.Violations, v)
		return nil
	}
	overdue := func(t uint64, strict bool) *K {
		for _, e := range h.Obligations {
			if e.Obligation.pending() && (e.Obligation.Deadline < t || !strict && e.Obligation.Deadline == t) {
				k := e.Key
				return &k
			}
		}
		return nil
	}
	if k := overdue(h.Now, false); k != nil {
		if e := violation(k, "missing_in_prior_history", "observation"); e != nil {
			return empty, e
		}
	}
	for _, event := range events {
		k, kind := event.Key, event.Kind
		if !immutableLifecycleKey(reflect.ValueOf(k)) {
			return empty, &ModelError{"non-reflexive lifecycle key"}
		}
		var failure error
		fail := func(code string) {
			if failure == nil {
				failure = violation(&k, code, kind)
			}
		}
		n, exists := index[k]
		r, acquired := resources[k]
		if kind == "fence" {
			if m.Epoch == nil {
				return empty, &ModelError{"legacy history has no fence"}
			}
			if event.Time < h.RetiredBefore {
				failure = violation(nil, "fence_backwards", kind)
			} else {
				blocked := false
				for _, e := range h.Obligations {
					if m.Epoch(e.Key) < event.Time && e.Obligation.pending() {
						key := e.Key
						failure = violation(&key, "fence_pending_obligation", kind)
						blocked = true
						break
					}
				}
				if !blocked {
					for _, e := range h.Resources {
						if m.Epoch(e.Key) < event.Time && e.Acquired {
							key := e.Key
							failure = violation(&key, "fence_acquired_resource", kind)
							blocked = true
							break
						}
					}
				}
				if !blocked {
					h.Obligations = slices.DeleteFunc(h.Obligations, func(e LifecycleEntry[K]) bool { return m.Epoch(e.Key) < event.Time })
					h.Resources = slices.DeleteFunc(h.Resources, func(e LifecycleResource[K]) bool { return m.Epoch(e.Key) < event.Time })
					h.RetiredBefore = event.Time
					clear(index)
					clear(resources)
					for i, e := range h.Obligations {
						if _, ok := index[e.Key]; !ok {
							index[e.Key] = i
						}
					}
					for i, e := range h.Resources {
						if _, ok := resources[e.Key]; !ok {
							resources[e.Key] = i
						}
					}
				}
			}
		} else if m.Epoch != nil && kind != "tick" && m.Epoch(k) < h.RetiredBefore {
			fail("retired_epoch_reference")
		} else {
			switch kind {
			case "tick":
				if event.Time < h.Now {
					failure = violation(nil, "time_backwards", kind)
				} else {
					if key := overdue(event.Time, true); key != nil {
						failure = violation(key, "missing_before_advance", kind)
					}
					h.Now = event.Time
				}
			case "begin":
				if exists {
					fail("key_reused")
				} else {
					if len(h.Obligations) >= m.Limits.Obligations {
						return empty, &ModelError{"obligation limit; explicit fence required; verification incomplete"}
					}
					index[k] = len(h.Obligations)
					h.Obligations = append(h.Obligations, LifecycleEntry[K]{k, LifecycleObligation{Deadline: event.Time}})
					if event.Time < h.Now {
						fail("began_after_deadline")
					}
				}
			case "cancel":
				if exists {
					h.Obligations[n].Obligation.Cancelled = true
				} else {
					fail("cancel_without_obligation")
				}
			case "settle":
				if !exists {
					fail("settlement_without_obligation")
				} else {
					o := &h.Obligations[n].Obligation
					if o.Cancelled {
						fail("settlement_after_cancel")
					} else if o.Settled {
						fail("duplicate_settlement")
					} else {
						if m.Epoch == nil {
							o.Settled = true
						}
						if o.Deadline < h.Now {
							fail("settlement_after_deadline")
						}
						o.Settled = true
					}
				}
			case "publish":
				if !exists || h.Obligations[n].Obligation.Cancelled || !h.Obligations[n].Obligation.Settled {
					fail("stale_or_premature_publication")
				} else if m.Epoch != nil {
					if h.Obligations[n].Obligation.Published {
						fail("duplicate_publication")
					} else {
						h.Obligations[n].Obligation.Published = true
					}
				}
			case "acquire":
				if acquired {
					if h.Resources[r].Acquired {
						fail("resource_already_acquired")
					} else {
						h.Resources[r].Acquired = true
					}
				} else {
					if len(h.Resources) >= m.Limits.Resources {
						return empty, &ModelError{"resource limit; verification incomplete"}
					}
					resources[k] = len(h.Resources)
					h.Resources = append(h.Resources, LifecycleResource[K]{k, true})
				}
			case "release":
				if exists && h.Obligations[n].Obligation.pending() {
					fail("release_before_settlement")
				}
				if !acquired {
					fail("release_without_acquisition")
				} else if !h.Resources[r].Acquired {
					fail("duplicate_release")
				} else {
					h.Resources[r].Acquired = false
				}
			default:
				return empty, &ModelError{"unknown lifecycle event"}
			}
		}
		if failure != nil {
			return empty, failure
		}
		// Canonical order also selects the first overdue key, as in BTreeMap.
		if m.Compare != nil {
			slices.SortFunc(h.Obligations, func(a, b LifecycleEntry[K]) int { return m.Compare(a.Key, b.Key) })
			slices.SortFunc(h.Resources, func(a, b LifecycleResource[K]) int { return m.Compare(a.Key, b.Key) })
			clear(index)
			clear(resources)
			for i, e := range h.Obligations {
				if _, ok := index[e.Key]; !ok {
					index[e.Key] = i
				}
			}
			for i, e := range h.Resources {
				if _, ok := resources[e.Key]; !ok {
					resources[e.Key] = i
				}
			}
		}
	}
	if k := overdue(h.Now, false); k != nil {
		if e := violation(k, "missing_at_deadline", "observation"); e != nil {
			return empty, e
		}
	}
	return h, nil
}
func (m LifecycleMonitor[K]) Checks(h LifecycleHistory[K]) ([]Check, error) {
	if e := m.Validate(h); e != nil {
		return nil, e
	}
	prefix := "lifecycle"
	if m.Epoch != nil {
		prefix += ".indexed"
	}
	safe := len(h.Violations) == 0
	for _, o := range h.Obligations {
		if o.Obligation.pending() && o.Obligation.Deadline <= h.Now {
			safe = false
		}
	}
	c := Passed(prefix + ".safety")
	if !safe {
		c = Failed(prefix+".safety", func() string {
			codes := []string{}
			for _, v := range h.Violations {
				if m.Epoch == nil && v.Legacy != nil {
					codes = append(codes, *v.Legacy)
				} else {
					codes = append(codes, v.Code)
				}
			}
			if len(codes) == 0 {
				if m.Epoch == nil {
					return "required settlement missing at deadline"
				}
				return "missing_at_deadline"
			}
			return strings.Join(codes, "; ")
		}())
	}
	p := Passed(prefix + ".obligations")
	if n := h.Retention().ActiveObligations; n > 0 {
		p = Skipped(prefix+".obligations", fmt.Sprintf("%d pending obligations in finite history", n))
	}
	return []Check{c, p}, nil
}

// LifecycleOracle adapts classifiers to the existing independent Oracle API.
func LifecycleOracle[S, I, O any, K comparable](monitor LifecycleMonitor[K], identity Metadata, input func(I, Disposition) ([]LifecycleEvent[K], error), output func([]O) ([]LifecycleEvent[K], error)) Oracle[S, LifecycleHistory[K], I, O] {
	return Oracle[S, LifecycleHistory[K], I, O]{Metadata: func() (Metadata, error) {
		m := identity
		if monitor.Epoch != nil {
			m.Name = fmt.Sprintf("indexed-lifecycle-v1:%d:%s;limits=%d,%d,%d;diagnostics=native-codes-v1", len(m.Name), m.Name, monitor.Limits.Obligations, monitor.Limits.Resources, monitor.Limits.Violations)
		} else {
			m.Name = fmt.Sprintf("%s;history-limit=%d", m.Name, monitor.Limits.Obligations)
		}
		m.Build += ";lifecycle-native"
		return m, nil
	}, InitialState: func() (LifecycleHistory[K], error) { return LifecycleHistory[K]{}, nil }, CloneHistory: func(h LifecycleHistory[K]) LifecycleHistory[K] { return h.Clone() }, EqualHistory: func(a, b LifecycleHistory[K]) bool {
		if monitor.Epoch == nil {
			a = a.Clone()
			b = b.Clone()
			for j := range a.Violations {
				v := a.Violations[j]
				a.Violations[j] = LifecycleViolation[K]{Legacy: v.Legacy}
			}
			for j := range b.Violations {
				v := b.Violations[j]
				b.Violations[j] = LifecycleViolation[K]{Legacy: v.Legacy}
			}
		}
		if len(a.Obligations) == 0 {
			a.Obligations = nil
		}
		if len(b.Obligations) == 0 {
			b.Obligations = nil
		}
		if len(a.Resources) == 0 {
			a.Resources = nil
		}
		if len(b.Resources) == 0 {
			b.Resources = nil
		}
		if len(a.Violations) == 0 {
			a.Violations = nil
		}
		if len(b.Violations) == 0 {
			b.Violations = nil
		}
		return reflect.DeepEqual(a, b)
	}, Advance: func(h LifecycleHistory[K], i I, o []O, d Disposition) (LifecycleHistory[K], error) {
		a, e := input(i, d)
		if e != nil {
			return LifecycleHistory[K]{}, e
		}
		b, e := output(o)
		if e != nil {
			return LifecycleHistory[K]{}, e
		}
		return monitor.Advance(h, append(slices.Clone(a), b...))
	}, CheckState: func(h LifecycleHistory[K], _ S) ([]Check, error) { return monitor.Checks(h) }}
}

// Pointer/interface object identity cannot survive snapshots or restart. Keys
// may be scalar values, arrays or structs recursively containing those values.
func immutableLifecycleKey(v reflect.Value) bool {
	if !v.IsValid() {
		return true
	}
	switch v.Kind() {
	case reflect.Bool, reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64, reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Uintptr, reflect.String:
		return true
	case reflect.Float32, reflect.Float64:
		f := v.Float()
		return f == f
	case reflect.Array:
		for i := 0; i < v.Len(); i++ {
			if !immutableLifecycleKey(v.Index(i)) {
				return false
			}
		}
		return true
	case reflect.Struct:
		for i := 0; i < v.NumField(); i++ {
			if !immutableLifecycleKey(v.Field(i)) {
				return false
			}
		}
		return true
	default:
		return false
	}
}
