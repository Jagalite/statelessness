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
		fields[0].Segment = d.Segment{Kind: "map_key", KeyType: "string", Name: "name"}
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
	if r.Node.Incomplete != "redacted" {
		t.Fatal("parent hid redacted child", r.Node)
	}
	if d.Diff(r, r) != d.DiffUnknown {
		t.Fatal("redacted equality")
	}
	paged := d.DefaultInspectQuery()
	paged.Limit = 1
	partial, err := d.Inspect(inspected{&calls, "ok", false}, paged)
	if err != nil || partial.Node.Incomplete != "pagination" {
		t.Fatal("unvisited redaction affected first page", partial, err)
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
	for _, value := range []d.Scalar{{Kind: "f16", Decimal: "1"}, {Kind: "u8", Decimal: string([]byte{0xff})}, {Kind: "char", Text: string([]byte{0xed, 0xa0, 0x80})}} {
		if _, e := d.Inspect(scalarProjection{value}, d.DefaultInspectQuery()); e == nil {
			t.Fatalf("runtime invalid scalar accepted: %+v", value)
		}
	}
	for _, value := range []d.Scalar{{Kind: "u8", Decimal: "256"}, {Kind: "i8", Decimal: "label"}, {Kind: "f32", Decimal: "label", Bits: ^uint64(0)}, {Kind: "u64", Decimal: "18446744073709551615"}, {Kind: "u128", Decimal: "340282366920938463463374607431768211455"}, {Kind: "i128", Decimal: "-170141183460469231731687303715884105728"}, {Kind: "f64", Decimal: "NaN", Bits: 18444492273895866368}} {
		if _, e := d.Inspect(scalarProjection{value}, d.DefaultInspectQuery()); e != nil {
			t.Fatal(value, e)
		}
	}
}

func TestArbitraryFloatKindIdentity(t *testing.T) {
	value := d.FloatScalar("u8", "not-a-number", ^uint64(0))
	result, err := d.Inspect(scalarProjection{value}, d.DefaultInspectQuery())
	if err != nil || result.Node.Scalar == nil || *result.Node.Scalar != value || !result.Node.IsComplete() {
		t.Fatalf("float identity lost: %#v %v", result.Node, err)
	}
	if d.FloatScalar("f32", "1", 7) != (d.Scalar{Kind: "f32", Decimal: "1", Bits: 7}) {
		t.Fatal("legacy float representation changed")
	}
	query := d.DefaultInspectQuery()
	query.Limits.MaxValueBytes = 2
	bounded, err := d.Inspect(scalarProjection{value}, query)
	if err != nil || bounded.Node.Kind != "truncated" {
		t.Fatalf("float identity escaped value budget: %#v %v", bounded.Node, err)
	}
}

func TestArbitraryFloatValidatedWatchAndEquality(t *testing.T) {
	r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	value := d.FloatScalar("u8", "256", 0)
	c := d.ChangedWatch(nil)
	c.Trigger, c.Equals = "equals", &value
	ack, err := r.AddValidated(0, c, scalarProjection{value}, d.SnapshotID{})
	if err != nil || ack.ResolvedKind != "u8" {
		t.Fatalf("ack=%#v err=%v", ack, err)
	}
	r.Drain()
	if err = r.Observe(d.WatchContext{Snapshot: d.SnapshotID{Sequence: 1}}, func() d.InspectAdapter { return scalarProjection{d.Scalar{Kind: "u8", Decimal: "256"}} }); err != nil {
		t.Fatal(err)
	}
	events, err := r.Drain()
	if err != nil || len(events) != 0 {
		t.Fatalf("integer matched float: %#v %v", events, err)
	}
	if err = r.Observe(d.WatchContext{Snapshot: d.SnapshotID{Sequence: 2}}, func() d.InspectAdapter { return scalarProjection{value} }); err != nil {
		t.Fatal(err)
	}
	events, err = r.Drain()
	if err != nil || len(events) != 1 {
		t.Fatalf("float did not match: %#v %v", events, err)
	}
}

func TestDuplicateChildSegmentsAreUnknown(t *testing.T) {
	count := uint64(2)
	leaf := d.InspectNode{Kind: "scalar", Scalar: &d.Scalar{Kind: "bool", Bool: true}}
	node := d.InspectNode{Kind: "object", ChildCount: &count, Children: []d.ChildNode{
		{Segment: d.FieldPath("x"), Node: leaf},
		{Segment: d.Segment{Kind: "field", Name: "x", Index: 99}, Node: leaf},
	}}
	if node.IsComplete() || d.CompareNodes(&node, &node) != d.DiffUnknown {
		t.Fatal("duplicate path accepted")
	}
	node.Children[1].Segment = d.FieldPath("y")
	if !node.IsComplete() || d.CompareNodes(&node, &node) != d.DiffUnchanged {
		t.Fatal("distinct paths rejected")
	}
}

func TestInvalidChildSegmentCannotEstablishEquality(t *testing.T) {
	count := uint64(1)
	leaf := d.InspectNode{Kind: "scalar", Scalar: &d.Scalar{Kind: "bool", Bool: true}}
	for _, segment := range []d.Segment{
		{Kind: "future"}, {Kind: "field", Name: string([]byte{0xff})},
		{Kind: "map_key", KeyType: "char", Name: "ab"},
		{Kind: "map_key", KeyType: "bool", Decimal: "maybe"},
		{Kind: "map_key", KeyType: "future", Decimal: "1"},
	} {
		node := d.InspectNode{Kind: "object", ChildCount: &count, Children: []d.ChildNode{{Segment: segment, Node: leaf}}}
		if node.IsComplete() || d.CompareNodes(&node, &node) != d.DiffUnknown {
			t.Fatal("invalid path compared equal", segment)
		}
	}
}

type ambiguousProjection struct{ calls *int }

func (a ambiguousProjection) DisplaySchema() d.DisplaySchema { return d.DisplaySchema{Version: 1} }
func (a ambiguousProjection) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	value := func(p []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
		*a.calls++
		return c.String(p, "value")
	}
	return c.Object(path, "ambiguous", []d.InspectField{{Segment: d.FieldPath("x"), Value: value}, {Segment: d.Segment{Kind: "field", Name: "x", Index: 99}, Value: value}})
}
func TestAmbiguousSelectedFieldDoesNotInvokeValues(t *testing.T) {
	calls := 0
	q := d.DefaultInspectQuery()
	q.Path = []d.Segment{d.FieldPath("x")}
	if _, err := d.Inspect(ambiguousProjection{&calls}, q); err == nil || calls != 0 {
		t.Fatal("ambiguous lookup", err, calls)
	}
	q.Limits.MaxWork = 1
	result, err := d.Inspect(ambiguousProjection{&calls}, q)
	if err != nil || result.Node.Incomplete != "work" || calls != 0 {
		t.Fatal("unverified lookup under budget", result, err, calls)
	}
}

func TestInvalidScalarAndTextCannotEstablishEquality(t *testing.T) {
	bad := string([]byte{0xff})
	for _, node := range []d.InspectNode{
		{Kind: "scalar", Scalar: &d.Scalar{Kind: "future"}},
		{Kind: "scalar", Scalar: &d.Scalar{Kind: "u8", Decimal: bad}},
		{Kind: "scalar", Scalar: &d.Scalar{Kind: "char", Text: bad}},
		{Kind: "string", Text: bad, TotalBytes: 1},
		{Kind: "object", Name: bad, ChildCount: new(uint64)},
	} {
		if node.IsComplete() || d.CompareNodes(&node, &node) != d.DiffUnknown {
			t.Fatal("invalid node compared equal", node)
		}
	}
	valid := d.InspectNode{Kind: "scalar", Scalar: &d.Scalar{Kind: "u8", Decimal: "255"}}
	if !valid.IsComplete() || d.CompareNodes(&valid, &valid) != d.DiffUnchanged {
		t.Fatal("valid scalar rejected")
	}
}
