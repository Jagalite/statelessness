package debug_test

import (
	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
	"testing"
)

func TestPassiveCursorAndExplicitDecode(t *testing.T) {
	v, e := d.NewEvidenceViewer(fixture(), s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	cursor, e := d.NewTraceViewer(v)
	if e != nil {
		t.Fatal(e)
	}
	if cursor.Position() != 0 || string(cursor.Current().After) != "10" || cursor.Previous() {
		t.Fatal("initial cursor")
	}
	if _, ok := cursor.InputPreview(1); ok {
		t.Fatal("initial input")
	}
	if !cursor.Next() || cursor.Next() {
		t.Fatal("next")
	}
	p, ok := cursor.OutputPreview(0, 1)
	if !ok || p.Complete || string(p.Bytes) != "1" {
		t.Fatal(p)
	}
	count := 0
	g := model(&count)
	state, e := d.DecodeState(cursor, g.Model, 0)
	if e != nil || state != 10 || count != 0 || cursor.Position() != 1 {
		t.Fatal(state, e)
	}
	g.Codec.Metadata = func() (s.Metadata, error) { return s.Identity("different", "manual"), nil }
	if _, e = d.DecodeState(cursor, g.Model, 0); e == nil {
		t.Fatal("decoder identity")
	}
	if !cursor.Previous() || cursor.Position() != 0 {
		t.Fatal("previous")
	}
}
