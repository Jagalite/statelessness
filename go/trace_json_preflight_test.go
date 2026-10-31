package statelessness

import (
	"strings"
	"testing"
)

func TestMarshalTracePreflightsBeforeHexExpansion(t *testing.T) {
	trace := &Trace{
		Metadata:      Identity("model", "build"),
		InitialState:  make([]byte, DefaultTraceLimits().MaxBlobBytes+1),
		InitialChecks: []Check{{ID: "invalid", Status: "passed", Details: "invalid"}},
		Termination:   "completed",
	}
	_, err := MarshalTrace(trace)
	if err == nil || !strings.Contains(err.Error(), "hexadecimal byte limit") {
		t.Fatalf("expected preflight blob limit before invalid check, got %v", err)
	}
}

func TestTraceJSONPreflightBoundsAndExactOutput(t *testing.T) {
	trace := &Trace{Metadata: Identity("é", "build"), InitialState: []byte{1, 2}, Termination: "completed"}
	encoded, err := MarshalTrace(trace)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := ParseTrace(encoded); err != nil {
		t.Fatal(err)
	}
	limits := DefaultTraceLimits()
	limits.MaxJSONBytes = len("ébuildcompleted") + 4
	if err := preflightTraceJSON(trace, limits); err != nil {
		t.Fatalf("lower bound at limit: %v", err)
	}
	limits.MaxJSONBytes--
	if err := preflightTraceJSON(trace, limits); err == nil {
		t.Fatal("expected lower bound limit")
	}
	limits = DefaultTraceLimits()
	limits.MaxPayloadBytes = 1
	if err := preflightTraceJSON(trace, limits); err == nil {
		t.Fatal("expected aggregate payload limit")
	}
}
