package debug_test

import (
	"fmt"
	"strings"
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

type indexedObjectFixture struct {
	total           uint64
	duplicate       bool
	entries, values *int
}

func (indexedObjectFixture) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "object", Version: 1}
}
func (f indexedObjectFixture) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	return c.ObjectIndexed(path, "object", f.total, func(i uint64) (d.InspectField, error) {
		*f.entries++
		name := fmt.Sprint(i)
		if f.duplicate && i == 3 {
			name = "1"
		}
		return d.InspectField{Segment: d.FieldPath(name), Label: name, Value: func(rest []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
			*f.values++
			return c.Scalar(rest, d.Scalar{Kind: "u8", Decimal: "7"})
		}}, nil
	})
}
func TestIndexedObjectBoundsPagesAndUniqueSelection(t *testing.T) {
	entries, values := 0, 0
	fixture := indexedObjectFixture{total: 1_000_000_000, entries: &entries, values: &values}
	q := d.DefaultInspectQuery()
	q.Offset = 999_999_999
	q.Limit = 1
	result, err := d.Inspect(fixture, q)
	if err != nil || entries != 1 || values != 1 || result.Node.Kind != "object" || result.Node.Name != "object" || result.Node.Children[0].Segment.Name != "999999999" {
		t.Fatal(result, err, entries, values)
	}
	entries, values = 0, 0
	q.Path = []d.Segment{d.FieldPath("0")}
	q.Limits.MaxWork = 3
	result, err = d.Inspect(fixture, q)
	if err != nil || result.Node.Incomplete != "work" || entries != 3 || values != 0 {
		t.Fatal(result, err, entries, values)
	}
	entries, values = 0, 0
	fixture.total = 5
	fixture.duplicate = true
	q.Limits.MaxWork = 100
	q.Path = []d.Segment{d.FieldPath("1")}
	if _, err = d.Inspect(fixture, q); err == nil || values != 0 {
		t.Fatal("ambiguous field invoked value", err, entries, values)
	}
	entries, values = 0, 0
	fixture.duplicate = false
	result, err = d.Inspect(fixture, q)
	if err != nil || entries != 5 || values != 1 || result.Node.Scalar.Decimal != "7" {
		t.Fatal(result, err, entries, values)
	}
	entries, values = 0, 0
	q.Path = nil
	q.Limits.MaxDepth = 0
	result, err = d.Inspect(fixture, q)
	if err != nil || result.Node.Incomplete != "depth" || entries != 0 || values != 0 {
		t.Fatal(result, err, entries, values)
	}
}

type invalidIndexedMetadataFixture struct {
	kind, metadata string
	values         *int
}

func (invalidIndexedMetadataFixture) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "invalid-meta", Version: 1}
}
func (f invalidIndexedMetadataFixture) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	entry := func(uint64) (d.InspectField, error) {
		key := d.FieldPath("x")
		if f.kind == "map" {
			key = d.Segment{Kind: "map_key", KeyType: "string", Name: "x"}
		}
		field := d.InspectField{Segment: key, Label: "x", Value: func(rest []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
			*f.values++
			return c.Scalar(rest, d.Scalar{Kind: "u8", Decimal: "1"})
		}}
		if f.metadata == "label" {
			field.Label = string([]byte{0xff})
		} else {
			field.Source = &d.SourceSite{File: string([]byte{0xff})}
		}
		return field, nil
	}
	if f.kind == "map" {
		return c.MapIndexed(path, "stable", 1, entry)
	}
	return c.ObjectIndexed(path, "object", 1, entry)
}
func TestIndexedMetadataValidatedBeforeValueCallback(t *testing.T) {
	for _, kind := range []string{"object", "map"} {
		for _, metadata := range []string{"label", "source"} {
			for _, selected := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/%s/selected=%v", kind, metadata, selected), func(t *testing.T) {
					values := 0
					q := d.DefaultInspectQuery()
					if selected {
						if kind == "map" {
							q.Path = []d.Segment{{Kind: "map_key", KeyType: "string", Name: "x"}}
						} else {
							q.Path = []d.Segment{d.FieldPath("x")}
						}
					}
					if _, err := d.Inspect(invalidIndexedMetadataFixture{kind: kind, metadata: metadata, values: &values}, q); err == nil || values != 0 {
						t.Fatal("invalid metadata ran getter", err, values)
					}
				})
			}
		}
	}
}

type invalidLegacyMetadataFixture struct {
	kind, metadata string
	values         *int
}

func (invalidLegacyMetadataFixture) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "legacy-invalid-meta", Version: 1}
}
func (f invalidLegacyMetadataFixture) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	key := d.FieldPath("x")
	if f.kind == "map" {
		key = d.Segment{Kind: "map_key", KeyType: "string", Name: "x"}
	} else if f.kind == "sequence" {
		key = d.IndexPath(0)
	}
	field := d.InspectField{Segment: key, Label: "x", Value: func(rest []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
		*f.values++
		return c.Scalar(rest, d.Scalar{Kind: "u8", Decimal: "1"})
	}}
	if f.metadata == "large-label" {
		field.Label = strings.Repeat("x", 8192)
	} else if f.metadata == "large-source" {
		field.Source = &d.SourceSite{File: strings.Repeat("x", 8192)}
	} else if f.metadata == "label" {
		field.Label = string([]byte{0xff})
	} else {
		field.Source = &d.SourceSite{File: string([]byte{0xff})}
	}
	switch f.kind {
	case "map":
		return c.Map(path, "stable", []d.InspectField{field})
	case "sequence":
		return c.Sequence(path, "slice", []d.InspectField{field})
	default:
		return c.Object(path, "object", []d.InspectField{field})
	}
}
func TestLegacyMetadataValidatedBeforeValueCallback(t *testing.T) {
	for _, kind := range []string{"object", "map"} {
		for _, metadata := range []string{"label", "source"} {
			for _, selected := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/%s/selected=%v", kind, metadata, selected), func(t *testing.T) {
					values := 0
					q := d.DefaultInspectQuery()
					if selected {
						switch kind {
						case "map":
							q.Path = []d.Segment{{Kind: "map_key", KeyType: "string", Name: "x"}}
						case "sequence":
							q.Path = []d.Segment{d.IndexPath(0)}
						default:
							q.Path = []d.Segment{d.FieldPath("x")}
						}
					}
					if _, err := d.Inspect(invalidLegacyMetadataFixture{kind: kind, metadata: metadata, values: &values}, q); err == nil || values != 0 {
						t.Fatal("invalid metadata ran getter", err, values)
					}
				})
			}
		}
	}
}

func TestSequenceIgnoresCallerLabelAndSource(t *testing.T) {
	for _, metadata := range []string{"label", "source", "large-label", "large-source"} {
		for _, selected := range []bool{false, true} {
			values := 0
			q := d.DefaultInspectQuery()
			if selected {
				q.Path = []d.Segment{d.IndexPath(0)}
			}
			r, err := d.Inspect(invalidLegacyMetadataFixture{kind: "sequence", metadata: metadata, values: &values}, q)
			if err != nil || values != 1 {
				t.Fatalf("metadata %s selected %v: %v calls %d", metadata, selected, err, values)
			}
			if !selected && (len(r.Node.Children) != 1 || r.Node.Children[0].Label != "0" || r.Node.Children[0].Source != nil) {
				t.Fatalf("noncanonical sequence: %#v", r.Node)
			}
		}
	}
}
