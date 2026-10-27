package debug_test

import (
	d "github.com/Jagalite/statelessness/go/debug"
	"testing"
)

type inspected struct {
	secret   *int
	text     string
	unstable bool
}

func (v inspected) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "manual", Version: 1}
}
func (v inspected) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	fields := []d.InspectField{{Segment: d.FieldPath("name"), Label: "name", Value: func(p []d.Segment, cx *d.InspectContext) (d.InspectNode, error) { return cx.String(p, v.text) }}, {Segment: d.FieldPath("secret"), Redacted: true, Value: func([]d.Segment, *d.InspectContext) (d.InspectNode, error) {
		*v.secret++
		panic("redacted callback ran")
	}}}
	if v.unstable {
		return c.Map(path, "unstable", fields[:1])
	}
	return c.Object(path, "manual", fields)
}
func TestInspectionRedactionAndUnknown(t *testing.T) {
	calls := 0
	q := d.DefaultInspectQuery()
	r, e := d.Inspect(inspected{&calls, "éclair", false}, q)
	if e != nil || calls != 0 || r.Node.Children[1].Node.Kind != "redacted" {
		t.Fatal(r, e, calls)
	}
	if d.Diff(r, r) != d.DiffUnknown {
		t.Fatal("redacted equality")
	}
	q.Path = []d.Segment{d.FieldPath("name")}
	r, e = d.Inspect(inspected{&calls, "éclair", false}, q)
	if e != nil || r.Node.Text != "éclair" || d.Diff(r, r) != d.DiffUnchanged {
		t.Fatal(r, e)
	}
	q.Limits.MaxValueBytes = 1
	q.Path = nil
	truncated, e := d.Inspect(textProjection("éclair"), q)
	if e != nil || truncated.Node.Text != "" || truncated.Node.Incomplete != "value_limit" || d.Diff(truncated, truncated) != d.DiffUnknown {
		t.Fatal(truncated, e)
	}
	q = d.DefaultInspectQuery()
	r, e = d.Inspect(inspected{&calls, "é", true}, q)
	if e != nil || r.Node.Ordering != "unstable" || d.Diff(r, r) != d.DiffUnknown {
		t.Fatal(r, e)
	}
}
func TestInspectionBudgetsSchemaAndPage(t *testing.T) {
	calls := 0
	q := d.DefaultInspectQuery()
	q.Limit = 1
	r, e := d.Inspect(inspected{&calls, "ok", false}, q)
	if e != nil || r.Node.Page.NextOffset == nil || *r.Node.Page.NextOffset != 1 || r.Node.IsComplete() {
		t.Fatal(r, e)
	}
	q.Limits.MaxWork = 0
	r, e = d.Inspect(inspected{&calls, "ok", false}, q)
	if e != nil || r.Node.Incomplete != "work" || calls != 0 {
		t.Fatal(r, e)
	}
	q = d.DefaultInspectQuery()
	q.SchemaVersion = 2
	if _, e = d.Inspect(inspected{&calls, "ok", false}, q); e == nil {
		t.Fatal("schema accepted")
	}
}

type textProjection string

func (textProjection) DisplaySchema() d.DisplaySchema { return d.DisplaySchema{Version: 1} }
func (v textProjection) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	return c.String(path, string(v))
}
func TestIncompleteContainersBeforeEquality(t *testing.T) {
	node := d.InspectNode{Kind: "sequence"}
	if node.IsComplete() || d.CompareNodes(&node, &node) != d.DiffUnknown {
		t.Fatal("missing child count accepted")
	}
	node = d.InspectNode{Kind: "scalar"}
	if node.IsComplete() {
		t.Fatal("missing scalar payload accepted")
	}
	node = d.InspectNode{Kind: "future"}
	if node.IsComplete() {
		t.Fatal("unknown node kind accepted")
	}
}

type scalarProjection struct{ value d.Scalar }

func (scalarProjection) DisplaySchema() d.DisplaySchema { return d.DisplaySchema{Version: 1} }
func (v scalarProjection) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	return c.Scalar(path, v.value)
}
func TestTypedScalarRuntimeAdmission(t *testing.T) {
	for _, value := range []d.Scalar{{Kind: "u8", Decimal: "256"}, {Kind: "i8", Decimal: "128"}, {Kind: "i8", Decimal: "-129"}, {Kind: "u128", Decimal: "340282366920938463463374607431768211456"}, {Kind: "f16", Decimal: "1"}, {Kind: "char", Text: string([]byte{0xed, 0xa0, 0x80})}} {
		if _, e := d.Inspect(scalarProjection{value}, d.DefaultInspectQuery()); e == nil {
			t.Fatalf("runtime invalid scalar accepted: %+v", value)
		}
	}
	for _, value := range []d.Scalar{{Kind: "u64", Decimal: "18446744073709551615"}, {Kind: "u128", Decimal: "340282366920938463463374607431768211455"}, {Kind: "i128", Decimal: "-170141183460469231731687303715884105728"}, {Kind: "f64", Decimal: "NaN", Bits: 18444492273895866368}} {
		if _, e := d.Inspect(scalarProjection{value}, d.DefaultInspectQuery()); e != nil {
			t.Fatal(value, e)
		}
	}
}
