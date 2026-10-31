package debug_test

import (
	"strings"
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

type permissivePathFixture struct{ calls *int }

func (permissivePathFixture) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "path", Version: 1}
}
func (f permissivePathFixture) Inspect(_ []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	*f.calls++
	return c.Scalar(nil, d.Scalar{Kind: "bool", Bool: true})
}

func TestInspectRejectsInvalidTypedQueryBeforeAdapter(t *testing.T) {
	for _, segment := range []d.Segment{
		{Kind: "map_key", KeyType: "char", Name: "ab"},
		{Kind: "map_key", KeyType: "unknown", Decimal: "256"},
		{Kind: "map_key", KeyType: "bool", Decimal: "yes"},
		{Kind: "field", Name: "\xff"},
		{Kind: "unknown"},
	} {
		calls := 0
		q := d.DefaultInspectQuery()
		q.Path = []d.Segment{segment}
		if _, err := d.Inspect(permissivePathFixture{&calls}, q); err == nil || calls != 0 {
			t.Fatalf("invalid path %#v reached adapter: error %v, calls %d", segment, err, calls)
		}
	}
}

func TestInspectPreservesArbitraryIntegerKeyText(t *testing.T) {
	for _, decimal := range []string{"256", "01", "+1", "", "not-a-number", strings.Repeat("9", 64)} {
		calls := 0
		q := d.DefaultInspectQuery()
		q.Path = []d.Segment{{Kind: "map_key", KeyType: "u8", Decimal: decimal}}
		if _, err := d.Inspect(permissivePathFixture{&calls}, q); err != nil || calls != 1 {
			t.Fatalf("decimal %q: error %v, calls %d", decimal, err, calls)
		}
	}
}
