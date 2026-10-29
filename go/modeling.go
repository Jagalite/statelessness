package statelessness

import (
	"fmt"
	"unicode/utf8"
)

// DomainDescriptor records application-owned environmental assumptions.
type DomainDescriptor struct {
	Name, Assumptions string
	MaxEntries        int
}

func (d DomainDescriptor) Parameters() [][2]string {
	return [][2]string{{"model.domain.name", d.Name}, {"model.domain.assumptions", d.Assumptions}, {"model.domain.max_entries", fmt.Sprint(d.MaxEntries)}}
}

// Domain owns snapshots; clone must deeply copy reference-containing inputs.
type Domain[I any] struct {
	descriptor DomainDescriptor
	entries    []I
	clone      func(I) I
}

func NewDomain[I any](d DomainDescriptor, clone func(I) I) (*Domain[I], error) {
	if d.MaxEntries < 0 || clone == nil || !utf8.ValidString(d.Name) || !utf8.ValidString(d.Assumptions) {
		return nil, &ModelError{"domain requires nonnegative limit and clone callback"}
	}
	return &Domain[I]{descriptor: d, clone: clone}, nil
}
func (d *Domain[I]) Descriptor() DomainDescriptor { return d.descriptor }
func (d *Domain[I]) Push(value I) error {
	if len(d.entries) >= d.descriptor.MaxEntries {
		return &ModelError{"domain entry limit exceeded"}
	}
	v, err := call("domain clone", func() (I, error) { return d.clone(value), nil })
	if err != nil {
		return err
	}
	if len(d.entries) >= d.descriptor.MaxEntries {
		return &ModelError{"domain entry limit exceeded"}
	}
	d.entries = append(d.entries, v)
	return nil
}

// Extend preserves the successful prefix, including on iterator failure.
func (d *Domain[I]) Extend(next Iterator[I]) error {
	for {
		item, err := call("domain next", func() (struct {
			v  I
			ok bool
		}, error) {
			v, ok, e := next()
			return struct {
				v  I
				ok bool
			}{v, ok}, e
		})
		if err != nil {
			return err
		}
		if !item.ok {
			return nil
		}
		if err = d.Push(item.v); err != nil {
			return err
		}
	}
}
func (d *Domain[I]) Entries() ([]I, error) {
	return call("domain clone", func() ([]I, error) {
		out := make([]I, len(d.entries))
		for i, v := range d.entries {
			out[i] = d.clone(v)
		}
		return out, nil
	})
}
func (d *Domain[I]) IntoEntries() []I { out := d.entries; d.entries = nil; return out }
func (d *Domain[I]) Contains(value I, equal func(I, I) bool) (bool, error) {
	return call("domain equality", func() (bool, error) {
		for _, v := range d.entries {
			if equal(d.clone(v), d.clone(value)) {
				return true, nil
			}
		}
		return false, nil
	})
}
func (d *Domain[I]) SampleIndexed(rng *Rng) (v I, ok bool, err error) {
	if rng == nil {
		return v, false, &ModelError{"rng required"}
	}
	index, ok := rng.Index(uint64(len(d.entries)))
	if ok {
		v, err = call("domain clone", func() (I, error) { return d.clone(d.entries[index]), nil })
	}
	return
}

// DomainProduct checks cardinality before invoking map. Callers own argument/result cloning.
func DomainProduct[A, B, I any](a []A, b []B, maximum int, mapper func(A, B) (I, error)) ([]I, error) {
	if maximum < 0 || len(b) > 0 && len(a) > maximum/len(b) {
		return nil, &ModelError{"domain product limit exceeded"}
	}
	out := make([]I, 0, len(a)*len(b))
	for _, x := range a {
		for _, y := range b {
			v, err := call("domain product", func() (I, error) { return mapper(x, y) })
			if err != nil {
				return nil, err
			}
			out = append(out, v)
		}
	}
	return out, nil
}
func CheckLazy(id string, passed bool, details func() (string, error)) (Check, error) {
	if passed {
		c := Passed(id)
		return c, c.Validate()
	}
	s, err := call("check details", details)
	if err != nil {
		return Check{}, err
	}
	c := Failed(id, s)
	return c, c.Validate()
}

// PropertyMetadata is a declaration, never execution/assurance evidence.
type PropertyMetadata struct{ ID, Phase string }

func DeclaredProperties(properties []PropertyMetadata) ([]PropertyMetadata, error) {
	seen := map[string]bool{}
	out := append([]PropertyMetadata(nil), properties...)
	for _, p := range out {
		if !utf8.ValidString(p.ID) {
			return nil, &ModelError{"property id must be UTF-8"}
		}
		if p.Phase != "state" && p.Phase != "transition" {
			return nil, &ModelError{"property phase must be state or transition"}
		}
		if seen[p.ID] {
			return nil, &ModelError{"duplicate property id"}
		}
		seen[p.ID] = true
	}
	return out, nil
}

// AdaptModel preserves the existing typed Model and codec binder. An explicit
// default factory avoids sharing mutable zero-state graphs across runs.
func AdaptModel[S, I, O any](m Model[S, I, O], properties []PropertyMetadata, initialDefault func() (S, error), unchecked bool) (Model[S, I, O], error) {
	if _, err := DeclaredProperties(properties); err != nil {
		return m, err
	}
	if len(properties) == 0 && !unchecked {
		return m, &ModelError{"no properties; select unchecked"}
	}
	if initialDefault != nil {
		if m.InitialState != nil {
			return m, &ModelError{"initial_default conflicts with initial callback"}
		}
		m.InitialState = initialDefault
	}
	if m.InitialState == nil || m.Step == nil {
		return m, &ModelError{"initial and step callbacks required"}
	}
	if m.CheckState == nil && m.CheckStateInto == nil {
		if !unchecked && m.CheckTransition == nil && m.CheckTransitionInto == nil {
			return m, &ModelError{"property checker required"}
		}
		m.CheckState = func(S) ([]Check, error) { return nil, nil }
	}
	return m, m.validate()
}
