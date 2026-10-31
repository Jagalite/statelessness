// Package debug provides optional display-only inspection for the request demo.
// Importing the demo model itself does not enable debugger instrumentation.
package debug

import (
	"slices"
	"strconv"

	d "github.com/Jagalite/statelessness/go/debug"
	"github.com/Jagalite/statelessness/go/demo"
)

type requestProjection struct{ state demo.State }

// RequestState returns an owned display snapshot for an explicit inspection
// or watch attachment; it does not use or reveal the model's exact codec.
// The snapshot copy is proportional to Pending's length and happens before
// inspection budgets apply. Query-time sequence construction is paged/lazy;
// callers must bound snapshot size if accepting arbitrary external states.
func RequestState(state demo.State) d.InspectAdapter {
	state.Pending = slices.Clone(state.Pending)
	return requestProjection{state: state}
}
func (requestProjection) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "request-lifecycle.State", Version: 1}
}
func (v requestProjection) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	scalar := func(value uint8) func([]d.Segment, *d.InspectContext) (d.InspectNode, error) {
		return func(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
			return c.Scalar(path, d.Scalar{Kind: "u8", Decimal: strconv.FormatUint(uint64(value), 10)})
		}
	}
	boolean := func(value bool) func([]d.Segment, *d.InspectContext) (d.InspectNode, error) {
		return func(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
			return c.Scalar(path, d.Scalar{Kind: "bool", Bool: value})
		}
	}
	sequence := func(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
		return c.SequenceIndexed(path, "slice", uint64(len(v.state.Pending)), func(index uint64, rest []d.Segment, cx *d.InspectContext) (d.InspectNode, error) {
			return scalar(v.state.Pending[index])(rest, cx)
		})
	}
	fields := []d.InspectField{{Segment: d.FieldPath("generation"), Label: "generation", Value: scalar(v.state.Generation)}, {Segment: d.FieldPath("active"), Label: "active", Value: boolean(v.state.Active)}, {Segment: d.FieldPath("ready"), Label: "ready", Value: boolean(v.state.Ready)}, {Segment: d.FieldPath("pending"), Label: "pending", Value: sequence}}
	return c.Object(path, "request-lifecycle.State", fields)
}
