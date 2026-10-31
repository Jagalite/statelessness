package statelessness_test

import (
	s "github.com/Jagalite/statelessness/go"
	"testing"
)

// Existing external unkeyed literals must remain source-compatible.
func TestLifecycleMonitorExternalLiteralCompatibility(t *testing.T) {
	m := s.LifecycleMonitor[int]{s.RetentionLimits{1, 1, 1}, nil, nil}
	if _, err := m.Checks(s.LifecycleHistory[int]{}); err != nil {
		t.Fatal(err)
	}
}
