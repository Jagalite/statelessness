package debug

import (
	"reflect"
	"strconv"
	"testing"
)

func TestPartialLeavesPreserveResourceLimit(t *testing.T) {
	for _, tc := range []struct {
		name   string
		invoke func(*InspectContext) (InspectNode, error)
	}{
		{"redacted field", func(c *InspectContext) (InspectNode, error) { return c.field(InspectField{Redacted: true}, nil) }},
		{"unavailable field", func(c *InspectContext) (InspectNode, error) { return c.field(InspectField{}, nil) }},
		{"unavailable leaf", func(c *InspectContext) (InspectNode, error) { return c.Unavailable(nil) }},
	} {
		for _, limit := range []string{"work", "nodes", "bytes"} {
			t.Run(tc.name+"/"+limit, func(t *testing.T) {
				limits := DefaultInspectLimits()
				switch limit {
				case "work":
					limits.MaxWork = 0
				case "nodes":
					limits.MaxNodes = 0
				case "bytes":
					limits.MaxBytes = 0
				}
				c := &InspectContext{limits: limits}
				n, err := tc.invoke(c)
				if err != nil || n.Kind != "truncated" || n.Incomplete != limit {
					t.Fatalf("got %#v, %v", n, err)
				}
			})
		}
	}
}

func TestContainersOmitResourceExhaustedChild(t *testing.T) {
	for _, kind := range []string{"sequence", "indexed-sequence", "object", "map", "indexed-object", "indexed-map"} {
		for _, reason := range []string{"work", "nodes", "bytes"} {
			t.Run(kind+"/"+reason, func(t *testing.T) {
				limits := DefaultInspectLimits()
				c := &InspectContext{limits: limits, limit: 2}
				calls := 0
				value := func(_ []Segment, c *InspectContext) (InspectNode, error) {
					calls++
					if calls == 2 {
						return engineTruncated(reason), nil
					}
					return c.Scalar(nil, Scalar{Kind: "u8", Decimal: "1"})
				}
				field := func(i uint64) InspectField {
					segment := FieldPath(string(rune('a' + i)))
					if kind == "sequence" {
						segment = IndexPath(i)
					}
					if kind == "map" || kind == "indexed-map" {
						segment = Segment{Kind: "map_key", KeyType: "string", Name: string(rune('a' + i))}
					}
					return InspectField{Segment: segment, Value: value}
				}
				var n InspectNode
				var err error
				switch kind {
				case "sequence":
					n, err = c.Sequence(nil, "", []InspectField{field(0), field(1)})
				case "indexed-sequence":
					n, err = c.SequenceIndexed(nil, "", 2, func(_ uint64, path []Segment, cx *InspectContext) (InspectNode, error) { return value(path, cx) })
				case "object":
					n, err = c.Object(nil, "", []InspectField{field(0), field(1)})
				case "map":
					n, err = c.Map(nil, "stable", []InspectField{field(0), field(1)})
				case "indexed-object":
					n, err = c.ObjectIndexed(nil, "", 2, func(i uint64) (InspectField, error) { return field(i), nil })
				case "indexed-map":
					n, err = c.MapIndexed(nil, "stable", 2, func(i uint64) (InspectField, error) { return field(i), nil })
				}
				if err != nil || n.Incomplete != reason || len(n.Children) != 1 || n.Page == nil || n.Page.Returned != 1 || n.Page.NextOffset == nil || *n.Page.NextOffset != 1 {
					t.Fatalf("got %#v, %v", n, err)
				}
			})
		}
	}
}

func TestContainersRetainExplicitTruncatedChild(t *testing.T) {
	for _, kind := range []string{"sequence", "object", "indexed-object"} {
		t.Run(kind, func(t *testing.T) {
			c := &InspectContext{limits: DefaultInspectLimits(), limit: 2}
			calls := 0
			value := func(_ []Segment, cx *InspectContext) (InspectNode, error) {
				calls++
				if calls == 1 {
					return InspectNode{Kind: "truncated", Incomplete: "nodes"}, nil
				}
				return cx.Scalar(nil, Scalar{Kind: "u8", Decimal: "1"})
			}
			fields := []InspectField{{Segment: FieldPath("x"), Value: value}, {Segment: FieldPath("y"), Value: value}}
			var n InspectNode
			var err error
			switch kind {
			case "sequence":
				n, err = c.SequenceIndexed(nil, "", 2, func(_ uint64, p []Segment, cx *InspectContext) (InspectNode, error) { return value(p, cx) })
			case "object":
				n, err = c.Object(nil, "", fields)
			case "indexed-object":
				n, err = c.ObjectIndexed(nil, "", 2, func(i uint64) (InspectField, error) { return fields[i], nil })
			}
			if err != nil || calls != 2 || len(n.Children) != 2 || n.Children[0].Node.Incomplete != "nodes" || n.Incomplete != "nodes" || n.Page.Returned != 2 || n.Page.NextOffset != nil {
				t.Fatalf("explicit child or following sibling lost: %#v, calls=%d, %v", n, calls, err)
			}
		})
	}
}

func TestChildOverheadChargedAfterValue(t *testing.T) {
	for _, kind := range []string{"sequence", "indexed-sequence", "object", "map", "indexed-object", "indexed-map"} {
		t.Run(kind, func(t *testing.T) {
			limits := DefaultInspectLimits()
			// Enough for the parent and scalar, but no child header.
			limits.MaxBytes = inspectNodeHeader + 40 + inspectNodeHeader + 64 + uint64(len("u8")+len("1"))
			c := &InspectContext{limits: limits, limit: 1}
			calls := 0
			value := func(path []Segment, cx *InspectContext) (InspectNode, error) {
				calls++
				return cx.Scalar(path, Scalar{Kind: "u8", Decimal: "1"})
			}
			segment := FieldPath("x")
			if kind == "sequence" {
				segment = IndexPath(0)
			}
			if kind == "map" || kind == "indexed-map" {
				segment = Segment{Kind: "map_key", KeyType: "string", Name: "x"}
			}
			field := InspectField{Segment: segment, Value: value}
			var n InspectNode
			var err error
			switch kind {
			case "sequence":
				n, err = c.Sequence(nil, "", []InspectField{field})
			case "indexed-sequence":
				n, err = c.SequenceIndexed(nil, "", 1, func(_ uint64, p []Segment, cx *InspectContext) (InspectNode, error) { return value(p, cx) })
			case "object":
				n, err = c.Object(nil, "", []InspectField{field})
			case "map":
				n, err = c.Map(nil, "stable", []InspectField{field})
			case "indexed-object":
				n, err = c.ObjectIndexed(nil, "", 1, func(uint64) (InspectField, error) { return field, nil })
			case "indexed-map":
				n, err = c.MapIndexed(nil, "stable", 1, func(uint64) (InspectField, error) { return field, nil })
			}
			if err != nil || calls != 1 || n.Incomplete != "bytes" || len(n.Children) != 0 || n.Page == nil || n.Page.NextOffset == nil || *n.Page.NextOffset != 0 || c.nodes != 1 || c.bytes != inspectNodeHeader+40 {
				t.Fatalf("callback/accounting: calls=%d node=%#v nodes=%d bytes=%d err=%v", calls, n, c.nodes, c.bytes, err)
			}
		})
	}
}

func TestChildSourceSnapshotPrecedesValueCallback(t *testing.T) {
	for _, kind := range []string{"object", "map", "indexed-object", "indexed-map"} {
		t.Run(kind, func(t *testing.T) {
			c := &InspectContext{limits: DefaultInspectLimits(), limit: 1}
			source := &SourceSite{File: "original.go", Line: 7, Column: 3}
			segment := FieldPath("x")
			if kind == "map" || kind == "indexed-map" {
				segment = Segment{Kind: "map_key", KeyType: "string", Name: "x"}
			}
			field := InspectField{Segment: segment, Source: source, Value: func(path []Segment, cx *InspectContext) (InspectNode, error) {
				source.File = string([]byte{0xff})
				source.Line = 99
				return cx.Scalar(path, Scalar{Kind: "u8", Decimal: "1"})
			}}
			var n InspectNode
			var err error
			switch kind {
			case "object":
				n, err = c.Object(nil, "", []InspectField{field})
			case "map":
				n, err = c.Map(nil, "stable", []InspectField{field})
			case "indexed-object":
				n, err = c.ObjectIndexed(nil, "", 1, func(uint64) (InspectField, error) { return field, nil })
			case "indexed-map":
				n, err = c.MapIndexed(nil, "stable", 1, func(uint64) (InspectField, error) { return field, nil })
			}
			if err != nil || len(n.Children) != 1 || n.Children[0].Source == nil || *n.Children[0].Source != (SourceSite{File: "original.go", Line: 7, Column: 3}) {
				t.Fatalf("source was changed by callback: %#v, %v", n, err)
			}
		})
	}
}

func TestOpaqueLabelUsesBoundedUTF8Preview(t *testing.T) {
	for _, tc := range []struct {
		max  uint64
		want string
	}{{0, ""}, {1, "a"}, {2, "a"}, {3, "aé"}, {4, "aéb"}} {
		limits := DefaultInspectLimits()
		limits.MaxValueBytes = tc.max
		c := &InspectContext{limits: limits}
		n, err := c.Opaque(nil, "aéb")
		if err != nil || n.Kind != "opaque" || n.Name != tc.want || n.Incomplete != "opaque" {
			t.Fatalf("max %d: %#v %v", tc.max, n, err)
		}
	}
	limits := DefaultInspectLimits()
	limits.MaxBytes = inspectNodeHeader
	c := &InspectContext{limits: limits}
	n, err := c.Opaque(nil, "a")
	if err != nil || !exhaustedChild(n) || n.Incomplete != "bytes" {
		t.Fatalf("byte exhaustion: %#v %v", n, err)
	}
	c = &InspectContext{limits: DefaultInspectLimits()}
	if _, err = c.Opaque(nil, string([]byte{0xff})); err == nil {
		t.Fatal("invalid UTF-8 accepted")
	}
}

func TestOpaqueValidatesSuffixWithinWorkBudget(t *testing.T) {
	limits := DefaultInspectLimits()
	limits.MaxValueBytes = 1
	c := &InspectContext{limits: limits}
	if _, err := c.Opaque(nil, "a"+string([]byte{0xff})); err == nil {
		t.Fatal("invalid UTF-8 beyond preview accepted")
	}
	limits.MaxWork = 2 // one node and one decoded rune
	c = &InspectContext{limits: limits}
	n, err := c.Opaque(nil, "ab"+string([]byte{0xff}))
	if err != nil || n.Kind != "truncated" || n.Incomplete != "work" {
		t.Fatalf("work-limited validation: %#v %v", n, err)
	}
}

func TestStringValidatesSuffixWithinWorkBudget(t *testing.T) {
	limits := DefaultInspectLimits()
	limits.MaxValueBytes = 1
	c := &InspectContext{limits: limits}
	if _, err := c.String(nil, "a"+string([]byte{0xff})); err == nil {
		t.Fatal("invalid UTF-8 beyond preview accepted")
	}
	limits.MaxWork = 2 // one node and one decoded rune
	c = &InspectContext{limits: limits}
	n, err := c.String(nil, "ab"+string([]byte{0xff}))
	if err != nil || n.Kind != "truncated" || n.Incomplete != "work" {
		t.Fatalf("work-limited validation: %#v %v", n, err)
	}
	limits = DefaultInspectLimits()
	limits.MaxValueBytes = 2 // 'é' straddles the byte limit after 'a'.
	c = &InspectContext{limits: limits}
	n, err = c.String(nil, "aéb")
	if err != nil || n.Kind != "string" || n.Text != "a" || n.TotalBytes != 4 || n.Incomplete != "value_limit" {
		t.Fatalf("UTF-8 boundary: %#v %v", n, err)
	}
}

func TestNonindexedContainerPageDoesNotScanSkippedEntries(t *testing.T) {
	for _, kind := range []string{"sequence", "object", "map"} {
		t.Run(kind, func(t *testing.T) {
			const total = 10000
			fields := make([]InspectField, total)
			calls := 0
			for i := range fields {
				segment := FieldPath(strconv.Itoa(i))
				if kind == "sequence" {
					segment = IndexPath(uint64(i))
				}
				if kind == "map" {
					segment = Segment{Kind: "map_key", KeyType: "string", Name: strconv.Itoa(i)}
				}
				fields[i] = InspectField{Segment: segment, Value: func(path []Segment, cx *InspectContext) (InspectNode, error) {
					calls++
					return cx.Scalar(path, Scalar{Kind: "u8", Decimal: "1"})
				}}
			}
			limits := DefaultInspectLimits()
			limits.MaxWork = 3
			c := &InspectContext{limits: limits, offset: total - 1, limit: 1}
			var n InspectNode
			var err error
			switch kind {
			case "sequence":
				n, err = c.Sequence(nil, "", fields)
			case "object":
				n, err = c.Object(nil, "", fields)
			case "map":
				n, err = c.Map(nil, "stable", fields)
			}
			if err != nil || calls != 1 || len(n.Children) != 1 || n.Page == nil || n.Page.Offset != total-1 || n.Page.Returned != 1 || c.work != 3 {
				t.Fatalf("page scanned skipped fields: calls=%d work=%d node=%#v err=%v", calls, c.work, n, err)
			}
		})
	}
}

func TestNonindexedSequenceSelectedIndexUsesDirectLookup(t *testing.T) {
	const total = 10000
	fields := make([]InspectField, total)
	calls := 0
	for i := range fields {
		fields[i] = InspectField{Segment: IndexPath(uint64(i)), Value: func(path []Segment, cx *InspectContext) (InspectNode, error) {
			calls++
			return cx.Scalar(path, Scalar{Kind: "u8", Decimal: "1"})
		}}
	}
	limits := DefaultInspectLimits()
	limits.MaxWork = 1
	c := &InspectContext{limits: limits, limit: 1}
	n, err := c.Sequence([]Segment{IndexPath(total - 1)}, "", fields)
	if err != nil || n.Scalar == nil || calls != 1 || c.work != 1 {
		t.Fatalf("direct lookup: %#v calls=%d work=%d err=%v", n, calls, c.work, err)
	}
	c = &InspectContext{limits: limits, limit: 1}
	if _, err = c.Sequence([]Segment{IndexPath(total)}, "", fields); err == nil || calls != 1 {
		t.Fatalf("out of range: %v, calls=%d", err, calls)
	}
	fields[total-1].Segment = IndexPath(0)
	c = &InspectContext{limits: limits, limit: 1}
	if _, err = c.Sequence([]Segment{IndexPath(total - 1)}, "", fields); err == nil || calls != 1 {
		t.Fatalf("mismatched entry: %v, calls=%d", err, calls)
	}
}

func TestSequenceMetadataMatchesIndexedProjection(t *testing.T) {
	limits := DefaultInspectLimits()
	value := func(path []Segment, c *InspectContext) (InspectNode, error) {
		return c.Scalar(path, Scalar{Kind: "u8", Decimal: "1"})
	}
	fields := []InspectField{{Segment: IndexPath(0), Label: "custom", Source: &SourceSite{File: "custom.go", Line: 1}, Value: value}}
	regular := &InspectContext{limits: limits, limit: 1}
	indexed := &InspectContext{limits: limits, limit: 1}
	a, err := regular.Sequence(nil, "slice", fields)
	if err != nil {
		t.Fatal(err)
	}
	b, err := indexed.SequenceIndexed(nil, "slice", 1, func(_ uint64, p []Segment, c *InspectContext) (InspectNode, error) { return value(p, c) })
	if err != nil || !reflect.DeepEqual(a, b) || a.Children[0].Label != "0" || a.Children[0].Source != nil {
		t.Fatalf("sequence projections differ: %#v %#v %v", a, b, err)
	}
}
