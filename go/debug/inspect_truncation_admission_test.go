package debug

import (
	"strings"
	"testing"
)

func TestTruncatedRootRejectsUnboundedReason(t *testing.T) {
	limits := DefaultInspectLimits()
	limits.MaxBytes = 0
	limits.MaxNodes = 0
	if _, err := cloneProjection(InspectNode{Kind: "truncated", Incomplete: strings.Repeat("x", 1<<20)}, limits); err == nil {
		t.Fatal("accepted an unbounded adapter-supplied truncation reason")
	}
	for _, reason := range []string{"work", "nodes", "bytes", "depth", "value_limit", "custom"} {
		got, err := cloneProjection(InspectNode{Kind: "truncated", Incomplete: reason}, limits)
		if err != nil || got.Incomplete != reason {
			t.Fatalf("reason %q: %#v, %v", reason, got, err)
		}
	}
}
