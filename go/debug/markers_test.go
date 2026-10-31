package debug

import (
	"strings"
	"testing"
)

func TestMarkerCollectorLazyBoundsAndUnicode(t *testing.T) {
	c, err := NewMarkerCollector(true, 2, 48+1+5)
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	if err := c.Marker("x", func() string { calls++; return "a🙂z" }); err != nil {
		t.Fatal(err)
	}
	if err := c.Marker("y", func() string { calls++; return "unused" }); err != nil {
		t.Fatal(err)
	}
	b, err := c.Finish()
	if err != nil {
		t.Fatal(err)
	}
	if calls != 1 || len(b.Markers) != 1 || b.Markers[0].Detail != "a🙂" || b.Truncated != 1 || b.Dropped != 1 || b.RetainedBytes != 54 {
		t.Fatalf("calls=%d batch=%+v", calls, b)
	}
	if _, err := c.Finish(); err == nil {
		t.Fatal("finish must consume collector")
	}
}
func TestMarkerCollectorRejectsBadTextAndReentry(t *testing.T) {
	c, _ := NewMarkerCollector(true, 2, 200)
	if err := c.Marker(string([]byte{0xff}), func() string { t.Fatal("called invalid id"); return "" }); err == nil {
		t.Fatal("invalid id accepted")
	}
	if err := c.Marker("a", func() string {
		if err := c.Marker("b", func() string { return "" }); err == nil {
			t.Error("reentry accepted")
		}
		if _, err := c.Finish(); err == nil {
			t.Error("finish during callback accepted")
		}
		return string([]byte{0xff})
	}); err == nil {
		t.Fatal("invalid detail accepted")
	}
	if err := c.Marker("ok", func() string { return strings.Repeat("x", 4) }); err != nil {
		t.Fatal(err)
	}
	b, err := c.Finish()
	if err != nil || len(b.Markers) != 1 || b.Markers[0].ID != "ok" {
		t.Fatalf("batch=%+v err=%v", b, err)
	}
}
func TestDisabledMarkerCollectorDoesNotInvokeDetail(t *testing.T) {
	c := DisabledMarkerCollector()
	if c.Interested() {
		t.Fatal("disabled collector interested")
	}
	if err := c.Marker("x", func() string { t.Fatal("disabled callback invoked"); return "" }); err != nil {
		t.Fatal(err)
	}
	b, err := c.Finish()
	if err != nil || len(b.Markers) != 0 || b.Dropped != 0 {
		t.Fatalf("batch=%+v err=%v", b, err)
	}
}

func TestMarkerCollectorRejectsOversizeBeforeTextValidation(t *testing.T) {
	c, _ := NewMarkerCollector(true, 1, 49)
	if err := c.Marker(string([]byte{0xff, 0xff}), nil); err != nil {
		t.Fatal(err)
	}
	b, err := c.Finish()
	if err != nil || b.Dropped != 1 || len(b.Markers) != 0 {
		t.Fatalf("batch=%+v err=%v", b, err)
	}
}
