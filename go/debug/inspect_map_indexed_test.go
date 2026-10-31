package debug_test

import (
	"fmt"
	"strings"
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

type indexedMapFixture struct {
	total           uint64
	duplicate       bool
	entries, values *int
}

func (indexedMapFixture) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "indexed-map", Version: 1}
}
func (f indexedMapFixture) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	return c.MapIndexed(path, "stable", f.total, func(i uint64) (d.InspectField, error) {
		*f.entries++
		key := fmt.Sprint(i)
		if f.duplicate && i == 3 {
			key = "1"
		}
		return d.InspectField{Segment: d.Segment{Kind: "map_key", KeyType: "string", Name: key}, Label: key, Value: func(rest []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
			*f.values++
			return c.Scalar(rest, d.Scalar{Kind: "u64", Decimal: "7"})
		}}, nil
	})
}
func TestIndexedMapPagesAndSelectedKeysAreBounded(t *testing.T) {
	entries, values := 0, 0
	model := indexedMapFixture{total: 1_000_000_000, entries: &entries, values: &values}
	q := d.DefaultInspectQuery()
	q.Offset = 999_999_999
	q.Limit = 1
	result, err := d.Inspect(model, q)
	if err != nil || entries != 1 || values != 1 || result.Node.Page.Total != model.total || result.Node.Children[0].Segment.Name != "999999999" {
		t.Fatal(result, err, entries, values)
	}
	entries, values = 0, 0
	q.Path = []d.Segment{{Kind: "map_key", KeyType: "string", Name: "0"}}
	q.Limits.MaxWork = 3
	result, err = d.Inspect(model, q)
	if err != nil || result.Node.Incomplete != "work" || entries != 3 || values != 0 {
		t.Fatal(result, err, entries, values)
	}
	entries, values = 0, 0
	model.total = 5
	model.duplicate = true
	q.Limits.MaxWork = 100
	q.Path[0].Name = "1"
	if _, err = d.Inspect(model, q); err == nil || values != 0 {
		t.Fatal("ambiguous key invoked value", err, entries, values)
	}
	entries, values = 0, 0
	model.duplicate = false
	result, err = d.Inspect(model, q)
	if err != nil || entries != 5 || values != 1 || result.Node.Scalar.Decimal != "7" {
		t.Fatal(result, err, entries, values)
	}
	entries, values = 0, 0
	q.Path = nil
	q.Limits.MaxDepth = 0
	result, err = d.Inspect(model, q)
	if err != nil || result.Node.Incomplete != "depth" || entries != 0 || values != 0 {
		t.Fatal(result, err, entries, values)
	}
}

type selectedMapAtDepthZero struct{}

func (selectedMapAtDepthZero) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "selected", Version: 1}
}
func (selectedMapAtDepthZero) Inspect(_ []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	key := d.Segment{Kind: "map_key", KeyType: "string", Name: "x"}
	return c.MapIndexed([]d.Segment{key}, "stable", 1, func(uint64) (d.InspectField, error) {
		return d.InspectField{Segment: key, Value: func(rest []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
			return c.Scalar(rest, d.Scalar{Kind: "u8", Decimal: "7"})
		}}, nil
	})
}
func TestIndexedMapSelectedKeyDoesNotConsumeDepth(t *testing.T) {
	q := d.DefaultInspectQuery()
	q.Limits.MaxDepth = 0
	r, err := d.Inspect(selectedMapAtDepthZero{}, q)
	if err != nil || r.Node.Scalar == nil || r.Node.Scalar.Decimal != "7" {
		t.Fatal(r, err)
	}
}

type rawKeyFixture struct {
	key    d.Segment
	label  string
	values *int
}

func (rawKeyFixture) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "raw-key", Version: 1}
}
func (f rawKeyFixture) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	return c.MapIndexed(path, "stable", 1, func(uint64) (d.InspectField, error) {
		return d.InspectField{Segment: f.key, Label: f.label, Value: func(rest []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
			*f.values++
			return c.Scalar(rest, d.Scalar{Kind: "u8", Decimal: "7"})
		}}, nil
	})
}
func TestIndexedMapRejectsNoncanonicalAndOversizedMetadata(t *testing.T) {
	invalid := []d.Segment{{Kind: "map_key", KeyType: "unknown", Decimal: "1"}, {Kind: "map_key", KeyType: "u8", Decimal: string([]byte{0xff})}}
	for _, key := range invalid {
		values := 0
		if _, err := d.Inspect(rawKeyFixture{key: key, values: &values}, d.DefaultInspectQuery()); err == nil || values != 0 {
			t.Fatal("invalid key admitted", err, values)
		}
	}
	values := 0
	large := rawKeyFixture{key: d.Segment{Kind: "map_key", KeyType: "string", Name: strings.Repeat("x", 4096)}, values: &values}
	q := d.DefaultInspectQuery()
	q.Limits.MaxValueBytes = 64
	q.Path = []d.Segment{{Kind: "map_key", KeyType: "string", Name: "x"}}
	result, err := d.Inspect(large, q)
	if err != nil || result.Node.Incomplete != "value_limit" || values != 0 {
		t.Fatal(result, err, values)
	}
}
func TestNumericMapKeyCompletenessPreservesText(t *testing.T) {
	count := uint64(1)
	leaf := d.InspectNode{Kind: "scalar", Scalar: &d.Scalar{Kind: "u8", Decimal: "1"}}
	for _, key := range []string{"01", "-0", "256"} {
		node := d.InspectNode{Kind: "map", Ordering: "stable", ChildCount: &count, Children: []d.ChildNode{{Segment: d.Segment{Kind: "map_key", KeyType: "u8", Decimal: key}, Node: leaf}}}
		if !node.IsComplete() {
			t.Fatal("valid text key marked incomplete", key)
		}
	}
	node := d.InspectNode{Kind: "map", Ordering: "stable", ChildCount: &count, Children: []d.ChildNode{{Segment: d.Segment{Kind: "map_key", KeyType: "u8", Decimal: "255"}, Node: leaf}}}
	if !node.IsComplete() {
		t.Fatal("valid key rejected")
	}
}
