package debug_test

import (
	"fmt"
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

type wrongKindFixture struct {
	kind  string
	calls *int
}

func (wrongKindFixture) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "kind", Version: 1}
}
func (f wrongKindFixture) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	segment := d.IndexPath(0)
	if f.kind == "sequence" {
		segment = d.FieldPath("x")
	}
	field := d.InspectField{Segment: segment, Label: "x", Value: func(rest []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
		*f.calls++
		return c.Scalar(rest, d.Scalar{Kind: "u8", Decimal: "1"})
	}}
	switch f.kind {
	case "object":
		return c.Object(path, "object", []d.InspectField{field})
	case "map":
		return c.Map(path, "stable", []d.InspectField{field})
	case "sequence":
		return c.Sequence(path, "slice", []d.InspectField{field})
	default:
		return c.Enum(path, "enum", "Case", "Case", []d.InspectField{field})
	}
}
func TestContainerRejectsWrongChildKindBeforeGetter(t *testing.T) {
	for _, kind := range []string{"object", "map", "sequence", "enum"} {
		for _, selected := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/%v", kind, selected), func(t *testing.T) {
				calls := 0
				q := d.DefaultInspectQuery()
				if selected {
					switch kind {
					case "object":
						q.Path = []d.Segment{d.FieldPath("x")}
					case "map":
						q.Path = []d.Segment{{Kind: "map_key", KeyType: "string", Name: "x"}}
					case "sequence":
						q.Path = []d.Segment{d.IndexPath(0)}
					case "enum":
						q.Path = []d.Segment{{Kind: "variant", Name: "Case"}, d.FieldPath("x")}
					}
				}
				if _, err := d.Inspect(wrongKindFixture{kind: kind, calls: &calls}, q); err == nil || calls != 0 {
					t.Fatal("invalid segment reached getter", err, calls)
				}
			})
		}
	}
}

type sparseSequenceFixture struct{ calls *int }

func (sparseSequenceFixture) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "sparse", Version: 1}
}
func (f sparseSequenceFixture) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	return c.Sequence(path, "slice", []d.InspectField{{Segment: d.IndexPath(3), Value: func(rest []d.Segment, cx *d.InspectContext) (d.InspectNode, error) {
		*f.calls++
		return cx.Scalar(rest, d.Scalar{Kind: "u8", Decimal: "1"})
	}}})
}

func TestSequenceRejectsNonpositionalIndex(t *testing.T) {
	for _, path := range [][]d.Segment{nil, {d.IndexPath(3)}, {d.IndexPath(0)}} {
		calls := 0
		q := d.DefaultInspectQuery()
		q.Path = path
		if _, err := d.Inspect(sparseSequenceFixture{&calls}, q); err == nil || calls != 0 {
			t.Fatalf("path %v: invalid sequence admitted: %v, calls %d", path, err, calls)
		}
	}
}

func TestSequenceCompletenessRequiresPositionalIndices(t *testing.T) {
	count := uint64(2)
	leaf := d.InspectNode{Kind: "scalar", Scalar: &d.Scalar{Kind: "bool", Bool: true}}
	node := d.InspectNode{Kind: "sequence", ChildCount: &count, Children: []d.ChildNode{
		{Segment: d.IndexPath(0), Node: leaf},
		{Segment: d.IndexPath(1), Node: leaf},
	}}
	if !node.IsComplete() || d.CompareNodes(&node, &node) != d.DiffUnchanged {
		t.Fatal("valid positional sequence rejected")
	}
	for _, indices := range [][2]uint64{{0, 3}, {1, 0}} {
		node.Children[0].Segment = d.IndexPath(indices[0])
		node.Children[1].Segment = d.IndexPath(indices[1])
		if node.IsComplete() || d.CompareNodes(&node, &node) != d.DiffUnknown {
			t.Fatalf("nonpositional sequence compared equal: %v", indices)
		}
	}
}

type enumFixture struct{ calls *int }

func (enumFixture) DisplaySchema() d.DisplaySchema { return d.DisplaySchema{Name: "enum", Version: 1} }
func (f enumFixture) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	return c.Enum(path, "Choice", "Some", "Some", []d.InspectField{{Segment: d.FieldPath("value"), Label: "value", Value: func(rest []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
		*f.calls++
		return c.Scalar(rest, d.Scalar{Kind: "u8", Decimal: "7"})
	}}})
}
func TestEnumProjectionUsesVariantChildAndSelectedPath(t *testing.T) {
	calls := 0
	q := d.DefaultInspectQuery()
	r, err := d.Inspect(enumFixture{&calls}, q)
	if err != nil || len(r.Node.Children) != 1 || r.Node.Children[0].Segment.Kind != "variant" || r.Node.Children[0].Node.Kind != "object" || len(r.Node.Children[0].Node.Children) != 1 || r.Node.Children[0].Node.Children[0].Node.Scalar == nil || r.Node.Children[0].Node.Children[0].Node.Scalar.Decimal != "7" {
		t.Fatal(r, err)
	}
	q.Path = []d.Segment{{Kind: "variant", Name: "Some"}, d.FieldPath("value")}
	r, err = d.Inspect(enumFixture{&calls}, q)
	if err != nil || r.Node.Scalar == nil || r.Node.Scalar.Decimal != "7" {
		t.Fatal(r, err)
	}
	before := calls
	q.Path = []d.Segment{d.FieldPath("value")}
	if _, err = d.Inspect(enumFixture{&calls}, q); err == nil || calls != before {
		t.Fatal("variant segment bypassed", err, calls)
	}
}

func TestCompletenessRequiresParentChildSegmentKind(t *testing.T) {
	count := uint64(1)
	leaf := d.InspectNode{Kind: "scalar", Scalar: &d.Scalar{Kind: "bool", Bool: true}}
	cases := []struct {
		kind    string
		segment d.Segment
	}{
		{"object", d.FieldPath("x")},
		{"sequence", d.IndexPath(0)},
		{"map", d.Segment{Kind: "map_key", KeyType: "string", Name: "x"}},
		{"enum", d.Segment{Kind: "variant", Name: "x"}},
	}
	for _, parent := range cases {
		for _, child := range cases {
			node := d.InspectNode{Kind: parent.kind, ChildCount: &count, Children: []d.ChildNode{{Segment: child.segment, Node: leaf}}}
			if parent.kind == "map" {
				node.Ordering = "stable"
			}
			valid := parent.kind == child.kind
			if node.IsComplete() != valid || (d.CompareNodes(&node, &node) == d.DiffUnchanged) != valid {
				t.Errorf("parent %s with %s child: completeness or comparison incorrect", parent.kind, child.kind)
			}
		}
	}
	leaf.Children = []d.ChildNode{{Segment: d.FieldPath("x"), Node: d.InspectNode{Kind: "scalar", Scalar: &d.Scalar{Kind: "bool"}}}}
	if leaf.IsComplete() || d.CompareNodes(&leaf, &leaf) != d.DiffUnknown {
		t.Fatal("scalar with children compared complete")
	}
}
