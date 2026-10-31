package statelessness

import (
	"cmp"
	"fmt"
	"strings"
	"unicode/utf8"
)

type lifecycleRustDebug[K comparable] struct {
	identity string
	key      func(K) (string, error)
	text     func(string) (string, error)
}

// IndexedRustDebugMonitor is an opt-in wrapper preserving the original monitor API.
type IndexedRustDebugMonitor[K cmp.Ordered] struct {
	Monitor LifecycleMonitor[ScopedKey[K]]
	debug   *lifecycleRustDebug[ScopedKey[K]]
}

// WithIndexedRustDebugDiagnostics opts an indexed monitor into Rust-style
// diagnostic text. formatKey must return the exact Rust Debug representation of
// the inner application key, including any quotes or type wrappers. formatText
// must return the exact Rust Debug representation of a string, including quotes.
// identity identifies both formatting contracts and participates in metadata.
// Formatter work and returned string sizes are controlled by the application.
func WithIndexedRustDebugDiagnostics[K cmp.Ordered](monitor LifecycleMonitor[ScopedKey[K]], identity string, formatKey func(K) (string, error), formatText func(string) (string, error)) (IndexedRustDebugMonitor[K], error) {
	if monitor.Epoch == nil || monitor.Compare == nil || identity == "" || !utf8.ValidString(identity) || formatKey == nil || formatText == nil {
		return IndexedRustDebugMonitor[K]{}, &ModelError{"indexed Rust diagnostics require an indexed monitor, formatter, and nonempty UTF-8 identity"}
	}
	debug := &lifecycleRustDebug[ScopedKey[K]]{identity: identity, key: func(k ScopedKey[K]) (string, error) {
		inner, err := formatKey(k.Key)
		if err != nil {
			return "", &ModelError{fmt.Sprintf("indexed lifecycle key formatter: %v", err)}
		}
		if !utf8.ValidString(inner) {
			return "", &ModelError{"indexed lifecycle key formatter returned invalid UTF-8"}
		}
		return fmt.Sprintf("ScopedKey { epoch: %d, generation: %d, key: %s }", k.Epoch, k.Generation, inner), nil
	}, text: func(s string) (string, error) {
		if !utf8.ValidString(s) {
			return "", &ModelError{"indexed lifecycle diagnostic string is not UTF-8"}
		}
		out, err := formatText(s)
		if err != nil {
			return "", &ModelError{fmt.Sprintf("indexed lifecycle string formatter: %v", err)}
		}
		if !utf8.ValidString(out) {
			return "", &ModelError{"indexed lifecycle string formatter returned invalid UTF-8"}
		}
		return out, nil
	}}
	return IndexedRustDebugMonitor[K]{Monitor: monitor, debug: debug}, nil
}

func lifecycleRustObligation(o LifecycleObligation) string {
	return fmt.Sprintf("Obligation { deadline: %d, cancelled: %t, settled: %t, published: %t }", o.Deadline, o.Cancelled, o.Settled, o.Published)
}

func (m *lifecycleRustDebug[K]) rustDebugRetention(r RetentionStatus[K]) (string, error) {
	oldest := "None"
	if r.OldestUnresolved != nil {
		key, err := m.key(*r.OldestUnresolved)
		if err != nil {
			return "", err
		}
		oldest = fmt.Sprintf("Some((%s, %d))", key, r.OldestDeadline)
	}
	return fmt.Sprintf("RetentionStatus { active_obligations: %d, historical_obligations: %d, acquired_resources: %d, historical_resources: %d, oldest_unresolved: %s, retired_before: %d }", r.ActiveObligations, r.HistoricalObligations, r.AcquiredResources, r.HistoricalResources, oldest, r.RetiredBefore), nil
}

func (m *lifecycleRustDebug[K]) indexedRustDebug(h LifecycleHistory[K]) (string, error) {
	var parts []string
	for _, v := range h.Violations {
		key, prior := "None", "None"
		if v.Key != nil {
			k, err := m.key(*v.Key)
			if err != nil {
				return "", err
			}
			key = "Some(" + k + ")"
		}
		if v.Prior != nil {
			prior = "Some(" + lifecycleRustObligation(*v.Prior) + ")"
		}
		code, err := m.text(v.Code)
		if err != nil {
			return "", err
		}
		event, err := m.text(v.Event)
		if err != nil {
			return "", err
		}
		parts = append(parts, fmt.Sprintf("Violation { key: %s, code: %s, event: %s, observed_at: %d, prior: %s }", key, code, event, v.ObservedAt, prior))
	}
	retention, err := m.rustDebugRetention(h.Retention())
	if err != nil {
		return "", err
	}
	return "violations=[" + strings.Join(parts, ", ") + "]; retention=" + retention, nil
}

// Checks computes the full batch from a detached history before publishing it.
func (m IndexedRustDebugMonitor[K]) Checks(h LifecycleHistory[ScopedKey[K]]) ([]Check, error) {
	if m.debug == nil || m.Monitor.Epoch == nil || m.Monitor.Compare == nil {
		return nil, &ModelError{"indexed Rust diagnostics require an indexed monitor"}
	}
	h = h.Clone()
	checks, err := m.Monitor.Checks(h)
	if err != nil {
		return nil, err
	}
	if checks[0].Status == "failed" {
		detail, e := m.debug.indexedRustDebug(h)
		if e != nil {
			return nil, e
		}
		checks[0].Details = detail
	}
	if checks[1].Status == "skipped" {
		detail, e := m.debug.rustDebugRetention(h.Retention())
		if e != nil {
			return nil, e
		}
		checks[1].Details = "finite history: " + detail
	}
	return checks, nil
}

// IndexedRustDebugOracle adapts the opt-in policy to an Oracle without changing
// the original LifecycleOracle or its metadata identity.
func IndexedRustDebugOracle[S, I, O any, K cmp.Ordered](monitor IndexedRustDebugMonitor[K], identity Metadata, input func(I, Disposition) ([]LifecycleEvent[ScopedKey[K]], error), output func([]O) ([]LifecycleEvent[ScopedKey[K]], error)) Oracle[S, LifecycleHistory[ScopedKey[K]], I, O] {
	base := LifecycleOracle[S, I, O](monitor.Monitor, identity, input, output)
	base.Metadata = func() (Metadata, error) {
		if monitor.debug == nil || monitor.Monitor.Epoch == nil || monitor.Monitor.Compare == nil {
			return Metadata{}, &ModelError{"indexed Rust diagnostics require an indexed monitor"}
		}
		m := identity
		m.Name = fmt.Sprintf("indexed-lifecycle-v1:%d:%s;limits=%d,%d,%d;diagnostics=rust-debug-v1;formatter=%d:%s", len(m.Name), m.Name, monitor.Monitor.Limits.Obligations, monitor.Monitor.Limits.Resources, monitor.Monitor.Limits.Violations, len(monitor.debug.identity), monitor.debug.identity)
		m.Build += ";lifecycle-native"
		return m, nil
	}
	base.CheckState = func(h LifecycleHistory[ScopedKey[K]], _ S) ([]Check, error) { return monitor.Checks(h) }
	return base
}
