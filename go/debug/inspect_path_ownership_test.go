package debug

import "testing"

type mutatingInspectPath struct{}

func (mutatingInspectPath) DisplaySchema() DisplaySchema { return DisplaySchema{Version: 1} }
func (mutatingInspectPath) Inspect(path []Segment, c *InspectContext) (InspectNode, error) {
	path[0].Name = "mutated"
	return c.String(nil, "value")
}

func TestInspectDetachesCallbackPath(t *testing.T) {
	path := []Segment{FieldPath("original")}
	q := InspectQuery{Path: path, SchemaVersion: 1, Limit: 1, Limits: DefaultInspectLimits()}
	result, err := Inspect(mutatingInspectPath{}, q)
	if err != nil {
		t.Fatal(err)
	}
	if path[0].Name != "original" || result.Path[0].Name != "original" {
		t.Fatal("inspection callback changed query or result path", path, result.Path)
	}
}

type panickingInspectAdapter struct{ schema bool }

func (p panickingInspectAdapter) DisplaySchema() DisplaySchema {
	if p.schema {
		panic("schema failure")
	}
	return DisplaySchema{Version: 1}
}
func (panickingInspectAdapter) Inspect([]Segment, *InspectContext) (InspectNode, error) {
	panic("projection failure")
}

func TestInspectReportsCallbackPanics(t *testing.T) {
	for _, schema := range []bool{true, false} {
		q := DefaultInspectQuery()
		q.Snapshot = SnapshotID{Session: 7, Sequence: 9}
		result, err := Inspect(panickingInspectAdapter{schema: schema}, q)
		if err == nil || result.Snapshot != q.Snapshot {
			t.Fatal("panic did not produce unavailable inspection", schema, result, err)
		}
	}
}
