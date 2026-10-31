package debug

import (
	s "github.com/Jagalite/statelessness/go"
	"testing"
)

func TestEffectWireSeparatesLabelAndAggregateDebuggerState(t *testing.T) {
	var fields []ProtocolField
	appendEffectFields(&fields, EffectTimeline{Labels: Labels{DebuggerAffected: false}, DebuggerAffected: true})
	values := make(map[string]string)
	positions := make(map[string]int)
	for i, field := range fields {
		if _, exists := values[field.Key]; exists {
			t.Fatalf("duplicate field %q", field.Key)
		}
		values[field.Key], positions[field.Key] = field.Value, i
	}
	if values["effect.labels.debugger_affected"] != "bool:false" || values["effect.debugger_affected"] != "bool:true" {
		t.Fatal(values)
	}
	if positions["effect.labels.debugger_affected"] >= positions["effect.complete"] || positions["effect.debugger_affected"] <= positions["effect.cancellation_acknowledged"] {
		t.Fatal(positions)
	}
}

func TestProtocolEventBudgetMinimum(t *testing.T) {
	limits := DefaultProtocolLimits()
	limits.MaxEventBytes = 511
	if limits.valid() {
		t.Fatal("undersized event budget accepted")
	}
	limits.MaxEventBytes = 512
	if !limits.valid() {
		t.Fatal("reference minimum rejected")
	}
}

// Rust protocol.rs event_size: size_of::<ProtocolEvent>() (88 on 64-bit)
// plus the session and kind bytes, independent of Go's struct layout.
func TestProtocolEventSizeFollowsRustAccounting(t *testing.T) {
	if got := protocolEventSize(protocolEvent{session: "abc", kind: "stepped", epoch: 1, sequence: 2}); got != 88+3+7 {
		t.Fatalf("event size %d", got)
	}
}

func TestStagedMetricStoreOwnsHistoryCosts(t *testing.T) {
	source := &MetricsStore{historyCosts: []int{3, 5}, historyBytes: 8}
	staged := copyMetricStore(source)
	staged.historyCosts[0] = 99
	if source.historyCosts[0] != 3 || source.historyBytes != 8 {
		t.Fatal("staged history mutated source")
	}
}

func TestReconnectReleasesMetricCapture(t *testing.T) {
	model := s.Model[int, byte, int]{InitialState: func() (int, error) { return 0, nil }, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[byte], CloneOutput: s.ValueCopy[int], Step: func(v int, _ byte) (s.Transition[int, int], error) { return s.Accept[int, int](v), nil }}
	session, err := NewSession("telemetry", model, Unrestricted[int, byte](), func(a, b byte) (bool, error) { return a == b, nil }, DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	p, err := NewProtocolSession(session, nil, ProtocolAuthority{}, DefaultProtocolLimits())
	if err != nil {
		t.Fatal(err)
	}
	options := DefaultEffectOptions()
	options.Timings = false
	observer, err := NewEffectObserver(options, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err = p.AttachEffectObserver(observer); err != nil {
		t.Fatal(err)
	}
	response := p.Handle(ProtocolRequest{Session: "telemetry", Epoch: p.Epoch(), ID: 1, Command: "metric_snapshot", Args: []string{"none", "u64:0", "u64:1"}})
	if response.Error != "" || p.telemetry == nil || p.telemetry.capture == nil {
		t.Fatal(response)
	}
	oldHandle := p.telemetry.handle
	p.Disconnect()
	if err = p.Reconnect(); err != nil {
		t.Fatal(err)
	}
	if p.telemetry.capture != nil || p.telemetry.handle != 0 || p.telemetry.epoch != 0 {
		t.Fatal("reconnect retained old metric capture")
	}
	response = p.Handle(ProtocolRequest{Session: "telemetry", Epoch: p.Epoch(), ID: 1, Command: "metric_snapshot", Args: []string{number(oldHandle), "u64:0", "u64:1"}})
	if response.Error != "stale_handle" {
		t.Fatal(response)
	}
}
