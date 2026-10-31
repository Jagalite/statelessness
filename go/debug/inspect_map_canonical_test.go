package debug_test

import (
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

type canonicalMapFixture struct {
	indexed, canonical bool
	calls              *int
}

func (canonicalMapFixture) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "map", Version: 1}
}
func (f canonicalMapFixture) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	field := d.InspectField{Segment: d.Segment{Kind: "map_key", KeyType: "string", Name: "key"}, Label: "custom", Source: &d.SourceSite{File: "source.go", Line: 1}, Value: func(p []d.Segment, cx *d.InspectContext) (d.InspectNode, error) {
		*f.calls++
		return cx.Scalar(p, d.Scalar{Kind: "u8", Decimal: "7"})
	}}
	if f.indexed {
		entry := func(uint64) (d.InspectField, error) { return field, nil }
		if f.canonical {
			return c.MapIndexedCanonical(path, "stable", 1, entry)
		}
		return c.MapIndexed(path, "stable", 1, entry)
	}
	if f.canonical {
		return c.MapCanonical(path, "stable", []d.InspectField{field})
	}
	return c.Map(path, "stable", []d.InspectField{field})
}

func TestCanonicalMapMetadataAndLegacyCompatibility(t *testing.T) {
	for _, indexed := range []bool{false, true} {
		for _, canonical := range []bool{false, true} {
			calls := 0
			q := d.DefaultInspectQuery()
			r, err := d.Inspect(canonicalMapFixture{indexed, canonical, &calls}, q)
			if err != nil || calls != 1 || len(r.Node.Children) != 1 {
				t.Fatal(r, err, calls)
			}
			child := r.Node.Children[0]
			if canonical && (child.Label != "" || child.Source != nil) {
				t.Fatal("noncanonical map metadata", child)
			}
			if !canonical && (child.Label != "custom" || child.Source == nil || child.Source.File != "source.go") {
				t.Fatal("legacy map metadata changed", child)
			}
			q.Path = []d.Segment{{Kind: "map_key", KeyType: "string", Name: "key"}}
			r, err = d.Inspect(canonicalMapFixture{indexed, canonical, &calls}, q)
			if err != nil || r.Node.Scalar == nil || r.Node.Scalar.Decimal != "7" || calls != 2 {
				t.Fatal(r, err, calls)
			}
		}
	}
}
