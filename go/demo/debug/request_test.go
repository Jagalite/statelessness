package debug_test

import (
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
	"github.com/Jagalite/statelessness/go/demo"
	inspect "github.com/Jagalite/statelessness/go/demo/debug"
)

func TestRequestProjectionOwnsStateAndExposesNestedPages(t *testing.T) {
	state := demo.State{Generation: 2, Active: true, Pending: []uint8{1, 2}}
	projection := inspect.RequestState(state)
	state.Pending[0] = 9
	q := d.DefaultInspectQuery()
	q.Path = []d.Segment{d.FieldPath("pending")}
	q.Limit = 1
	result, err := d.Inspect(projection, q)
	if err != nil || result.Node.Page == nil || result.Node.Page.Total != 2 || result.Node.Page.NextOffset == nil || *result.Node.Page.NextOffset != 1 || len(result.Node.Children) != 1 || result.Node.Children[0].Node.Scalar.Decimal != "1" {
		t.Fatal(result, err)
	}
	q.Path = append(q.Path, d.IndexPath(1))
	result, err = d.Inspect(projection, q)
	if err != nil || result.Node.Scalar.Decimal != "2" {
		t.Fatal(result, err)
	}
	q.Path = []d.Segment{d.FieldPath("active")}
	result, err = d.Inspect(projection, q)
	if err != nil || result.Node.Scalar.Kind != "bool" || !result.Node.Scalar.Bool {
		t.Fatal(result, err)
	}
}

func TestRequestProjectionRetainsInvalidCounterexamplesWithinCodecBound(t *testing.T) {
	state := demo.State{Generation: 2, Pending: []uint8{1, 2, 3}}
	q := d.DefaultInspectQuery()
	q.Path = []d.Segment{d.FieldPath("pending"), d.IndexPath(2)}
	result, err := d.Inspect(inspect.RequestState(state), q)
	if err != nil || result.Node.Scalar == nil || result.Node.Scalar.Decimal != "3" {
		t.Fatal("property-failing state became uninspectable", result, err)
	}
	state.Pending = make([]uint8, 255)
	state.Pending[254] = 7
	q.Path = []d.Segment{d.FieldPath("pending"), d.IndexPath(254)}
	result, err = d.Inspect(inspect.RequestState(state), q)
	if err != nil || result.Node.Scalar == nil || result.Node.Scalar.Decimal != "7" {
		t.Fatal("codec boundary became uninspectable", result, err)
	}
}

func TestRequestProjectionInspectsLargePendingSnapshot(t *testing.T) {
	state := demo.State{Pending: make([]uint8, 1_000_000)}
	state.Pending[999999] = 7
	projection := inspect.RequestState(state)
	state.Pending[999999] = 9
	q := d.DefaultInspectQuery()
	q.Path = []d.Segment{d.FieldPath("pending"), d.IndexPath(999999)}
	result, err := d.Inspect(projection, q)
	if err != nil || result.Node.Scalar == nil || result.Node.Scalar.Decimal != "7" {
		t.Fatal(result, err)
	}
	q.Path = []d.Segment{d.FieldPath("pending")}
	q.Offset = 999999
	q.Limit = 1
	result, err = d.Inspect(projection, q)
	if err != nil || len(result.Node.Children) != 1 || result.Node.Children[0].Node.Scalar.Decimal != "7" || result.Node.Page.Total != 1_000_000 {
		t.Fatal(result, err)
	}
}
