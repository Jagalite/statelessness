package debug_test

import (
	"fmt"
	"strconv"
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

type indexedFixture struct{ calls *int }

func (indexedFixture) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "indexed", Version: 1}
}
func (f indexedFixture) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	return c.SequenceIndexed(path, "slice", 1_000_000_000, func(index uint64, rest []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
		*f.calls++
		return c.Scalar(rest, d.Scalar{Kind: "u64", Decimal: "7"})
	})
}
func TestIndexedSequenceBoundsCallbacksToPage(t *testing.T) {
	calls := 0
	q := d.DefaultInspectQuery()
	q.Offset = 999_999_999
	q.Limit = 1
	result, err := d.Inspect(indexedFixture{&calls}, q)
	if err != nil || calls != 1 || result.Node.Page.Total != 1_000_000_000 || result.Node.Page.Offset != 999_999_999 || len(result.Node.Children) != 1 {
		t.Fatal(result, err, calls)
	}
	calls = 0
	q.Path = []d.Segment{d.IndexPath(999_999_999)}
	result, err = d.Inspect(indexedFixture{&calls}, q)
	if err != nil || calls != 1 || result.Node.Scalar.Decimal != "7" {
		t.Fatal(result, err, calls)
	}
	calls = 0
	q.Path = []d.Segment{d.IndexPath(1_000_000_000)}
	if _, err = d.Inspect(indexedFixture{&calls}, q); err == nil || calls != 0 {
		t.Fatal("out-of-range selected", err, calls)
	}
	calls = 0
	q.Path = nil
	q.Limits.MaxWork = 1
	result, err = d.Inspect(indexedFixture{&calls}, q)
	if err != nil || calls != 0 || result.Node.Incomplete != "work" {
		t.Fatal(result, err, calls)
	}
}

type nestedIndexedFixture struct{}

func (nestedIndexedFixture) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "nested", Version: 1}
}
func (nestedIndexedFixture) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	return c.SequenceIndexed(path, "outer", 3, func(_ uint64, _ []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
		return c.SequenceIndexed(nil, "inner", 3, func(i uint64, rest []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
			return c.Scalar(rest, d.Scalar{Kind: "u64", Decimal: strconv.FormatUint(i, 10)})
		})
	})
}
func TestIndexedSequenceResetsNestedPageOffset(t *testing.T) {
	q := d.DefaultInspectQuery()
	q.Offset = 1
	q.Limit = 2
	result, err := d.Inspect(nestedIndexedFixture{}, q)
	if err != nil || result.Node.Page.Offset != 1 || len(result.Node.Children) != 2 {
		t.Fatal(result, err)
	}
	for _, child := range result.Node.Children {
		if child.Node.Page == nil || child.Node.Page.Offset != 0 || len(child.Node.Children) != 2 || child.Node.Children[0].Node.Scalar.Decimal != "0" {
			t.Fatal("nested offset leaked", child)
		}
	}
}

type nestedFieldsFixture struct{ kind string }

func (nestedFieldsFixture) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "nested-fields", Version: 1}
}
func (f nestedFieldsFixture) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	field := func(i uint64) d.InspectField {
		segment := d.FieldPath(strconv.FormatUint(i, 10))
		if f.kind == "sequence" {
			segment = d.IndexPath(i)
		}
		if f.kind == "map" || f.kind == "indexed-map" {
			segment = d.Segment{Kind: "map_key", KeyType: "string", Name: strconv.FormatUint(i, 10)}
		}
		return d.InspectField{Segment: segment, Value: func(_ []d.Segment, cx *d.InspectContext) (d.InspectNode, error) {
			return cx.SequenceIndexed(nil, "inner", 3, func(i uint64, rest []d.Segment, cx *d.InspectContext) (d.InspectNode, error) {
				return cx.Scalar(rest, d.Scalar{Kind: "u64", Decimal: strconv.FormatUint(i, 10)})
			})
		}}
	}
	switch f.kind {
	case "sequence":
		return c.Sequence(path, "outer", []d.InspectField{field(0), field(1), field(2)})
	case "object":
		return c.Object(path, "outer", []d.InspectField{field(0), field(1), field(2)})
	case "map":
		return c.Map(path, "stable", []d.InspectField{field(0), field(1), field(2)})
	case "indexed-map":
		return c.MapIndexed(path, "stable", 3, func(i uint64) (d.InspectField, error) { return field(i), nil })
	default:
		return c.ObjectIndexed(path, "outer", 3, func(i uint64) (d.InspectField, error) { return field(i), nil })
	}
}

func TestFieldContainersResetNestedPageOffset(t *testing.T) {
	for _, kind := range []string{"sequence", "object", "map", "indexed-object", "indexed-map"} {
		t.Run(kind, func(t *testing.T) {
			q := d.DefaultInspectQuery()
			q.Offset = 1
			q.Limit = 2
			result, err := d.Inspect(nestedFieldsFixture{kind}, q)
			if err != nil || result.Node.Page == nil || result.Node.Page.Offset != 1 || len(result.Node.Children) != 2 {
				t.Fatal(result, err)
			}
			for _, child := range result.Node.Children {
				if child.Node.Page == nil || child.Node.Page.Offset != 0 || len(child.Node.Children) != 2 || child.Node.Children[0].Node.Scalar.Decimal != "0" {
					t.Fatal("nested page offset leaked", child)
				}
			}
		})
	}
}

type selectedIndexedFixture struct{}

func (selectedIndexedFixture) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "selected", Version: 1}
}
func (selectedIndexedFixture) Inspect(_ []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	return c.SequenceIndexed([]d.Segment{d.IndexPath(0)}, "slice", 1, func(_ uint64, rest []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
		return c.Scalar(rest, d.Scalar{Kind: "u8", Decimal: "7"})
	})
}
func TestIndexedSelectedPathDoesNotConsumeDepth(t *testing.T) {
	q := d.DefaultInspectQuery()
	q.Limits.MaxDepth = 0
	result, err := d.Inspect(selectedIndexedFixture{}, q)
	if err != nil || result.Node.Scalar == nil || result.Node.Scalar.Decimal != "7" {
		t.Fatal(result, err)
	}
}

type panicRestorationFixture struct{ returnError bool }

func (panicRestorationFixture) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "restore", Version: 1}
}
func (f panicRestorationFixture) Inspect(_ []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	func() {
		defer func() { _ = recover() }()
		_, _ = c.SequenceIndexed(nil, "panic", 3, func(uint64, []d.Segment, *d.InspectContext) (d.InspectNode, error) {
			if f.returnError {
				return d.InspectNode{}, fmt.Errorf("test")
			}
			panic("test")
		})
	}()
	return c.SequenceIndexed(nil, "after", 3, func(i uint64, rest []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
		return c.Scalar(rest, d.Scalar{Kind: "u64", Decimal: strconv.FormatUint(i, 10)})
	})
}
func TestIndexedSequenceRestoresContextAfterAccessorFailure(t *testing.T) {
	q := d.DefaultInspectQuery()
	q.Offset = 1
	q.Limit = 1
	for _, returnError := range []bool{false, true} {
		result, err := d.Inspect(panicRestorationFixture{returnError: returnError}, q)
		if err != nil || result.Node.Page == nil || result.Node.Page.Offset != 1 || len(result.Node.Children) != 1 || result.Node.Children[0].Node.Scalar.Decimal != "1" {
			t.Fatal(result, err)
		}
	}
}

type depthPreflightFixture struct {
	kind  string
	calls *int
}

func (depthPreflightFixture) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "depth", Version: 1}
}
func (f depthPreflightFixture) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	value := func(rest []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
		*f.calls++
		return c.Scalar(rest, d.Scalar{Kind: "u8", Decimal: "7"})
	}
	fields := []d.InspectField{{Segment: d.IndexPath(0), Label: "0", Value: value}}
	switch f.kind {
	case "object":
		fields[0].Segment = d.FieldPath("x")
		fields[0].Label = "x"
		return c.Object(path, "object", fields)
	case "sequence":
		return c.Sequence(path, "slice", fields)
	case "map":
		fields[0].Segment = d.Segment{Kind: "map_key", KeyType: "string", Name: "x"}
		return c.Map(path, "stable", fields)
	default:
		return c.SequenceIndexed(path, "slice", 1, func(_ uint64, rest []d.Segment, c *d.InspectContext) (d.InspectNode, error) { return value(rest, c) })
	}
}
func TestContainerDepthPreflightSkipsChildAccess(t *testing.T) {
	for _, kind := range []string{"object", "sequence", "map", "indexed"} {
		for _, offset := range []uint64{0, 9} {
			t.Run(fmt.Sprintf("%s/%d", kind, offset), func(t *testing.T) {
				calls := 0
				q := d.DefaultInspectQuery()
				q.Limits.MaxDepth = 0
				q.Offset = offset
				r, err := d.Inspect(depthPreflightFixture{kind: kind, calls: &calls}, q)
				if err != nil || calls != 0 || r.Node.Incomplete != "depth" || len(r.Node.Children) != 0 || r.Node.Page == nil || r.Node.Page.NextOffset == nil || *r.Node.Page.NextOffset != min(offset, uint64(1)) {
					t.Fatal(r, err, calls)
				}
			})
		}
	}
}
