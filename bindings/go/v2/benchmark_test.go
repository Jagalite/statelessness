//go:build cgo

package v2

import (
	"encoding/binary"
	"fmt"
	"testing"
)

// Fixtures share a 32-state graph, ordered inputs [1,2], modulo-32 transitions,
// one output containing the prior state, and the same stable checks. Large adds
// a 64KiB immutable payload; Expensive adds 4096 deterministic check iterations.
// These sources are compiled only until runtime benchmarking is authorized.
type benchmarkState struct {
	Value   uint32
	Payload []byte
}
type benchmarkCounters struct {
	callbacks, steps, snapshots, snapshotBytes, encodedBytes, retainedRoots uint64
	sink                                                                    uint64
}
type benchmarkFixture struct {
	name                     string
	payload, checkIterations int
}

var benchmarkFixtures = []benchmarkFixture{{"Cheap", 0, 0}, {"Large", 64 * 1024, 0}, {"Expensive", 0, 4096}}

func benchmarkModel(f benchmarkFixture, fail bool, c *benchmarkCounters) Model[benchmarkState, uint8, uint32] {
	hit := func() { c.callbacks++ }
	encodeState := func(s benchmarkState) ([]byte, error) {
		hit()
		b := make([]byte, 4+len(s.Payload))
		binary.LittleEndian.PutUint32(b, s.Value)
		copy(b[4:], s.Payload)
		c.encodedBytes += uint64(len(b))
		return b, nil
	}
	return Model[benchmarkState, uint8, uint32]{
		Metadata:     Metadata{Name: "abi-v2-matched-counter", Build: "benchmark-v1", ModelVersion: 1, PropertiesVersion: 1, CodecVersion: 1},
		InitialState: func() (benchmarkState, error) { hit(); return benchmarkState{Payload: make([]byte, f.payload)}, nil },
		Step: func(s benchmarkState, input uint8) (Transition[benchmarkState, uint32], error) {
			hit()
			c.steps++
			before := s.Value
			s.Value = (s.Value + uint32(input)) % 32
			return Accept(s, before), nil
		},
		CheckState: func(s benchmarkState) ([]Check, error) {
			hit()
			digest := uint64(s.Value)
			for i := 0; i < f.checkIterations; i++ {
				digest = (digest^uint64(i))*0x9e3779b97f4a7c15 + 1
			}
			c.sink ^= digest
			if s.Value >= 32 || len(s.Payload) != f.payload {
				return []Check{Failed("valid", "invalid state")}, nil
			}
			if fail && s.Value >= 24 {
				return []Check{Failed("bound", "state reached failure region")}, nil
			}
			return []Check{Passed("valid")}, nil
		},
		CheckTransition: func(before benchmarkState, input uint8, t Transition[benchmarkState, uint32]) ([]Check, error) {
			hit()
			if t.State.Value != (before.Value+uint32(input))%32 || len(t.Outputs) != 1 || t.Outputs[0] != before.Value {
				return []Check{Failed("transition", "inconsistent transition")}, nil
			}
			return []Check{Passed("transition")}, nil
		},
		CloneState: func(s benchmarkState) benchmarkState {
			hit()
			c.snapshots++
			c.snapshotBytes += uint64(len(s.Payload) + 4)
			s.Payload = append([]byte(nil), s.Payload...)
			return s
		},
		CloneInput:  func(i uint8) uint8 { hit(); c.snapshots++; c.snapshotBytes++; return i },
		CloneOutput: func(o uint32) uint32 { hit(); c.snapshots++; c.snapshotBytes += 4; return o },
		Inputs: func(benchmarkState) (Iterator[uint8], error) {
			hit()
			i := 0
			return Iterator[uint8]{Next: func() (uint8, bool, error) {
				hit()
				if i == 2 {
					return 0, false, nil
				}
				i++
				return uint8(i), true, nil
			}, Close: func() error { hit(); return nil }}, nil
		},
		EqualStates:         func(a, b benchmarkState) (bool, error) { hit(); return a.Value == b.Value, nil },
		HashState:           func(s benchmarkState) (uint64, error) { hit(); return uint64(s.Value), nil },
		EstimatedStateBytes: func(s benchmarkState) (uint64, bool, error) { hit(); return uint64(len(s.Payload) + 4), true, nil },
		Codec: &Codec[benchmarkState, uint8, uint32]{
			EncodeState: encodeState,
			DecodeState: func(b []byte) (benchmarkState, error) {
				hit()
				if len(b) != 4+f.payload {
					return benchmarkState{}, fmt.Errorf("invalid state length")
				}
				return benchmarkState{binary.LittleEndian.Uint32(b), append([]byte(nil), b[4:]...)}, nil
			},
			EncodeInput: func(i uint8) ([]byte, error) { hit(); c.encodedBytes++; return []byte{i}, nil },
			DecodeInput: func(b []byte) (uint8, error) {
				hit()
				if len(b) != 1 {
					return 0, fmt.Errorf("invalid input length")
				}
				return b[0], nil
			},
			EncodeOutput: func(o uint32) ([]byte, error) {
				hit()
				c.encodedBytes += 4
				b := make([]byte, 4)
				binary.LittleEndian.PutUint32(b, o)
				return b, nil
			},
		},
		Generate: func(_ benchmarkState, rng *CallbackRNG) (uint8, bool, error) {
			hit()
			value, ok, err := rng.Index(2)
			return uint8(value) + 1, ok, err
		},
		IsEnabled:   func(_ benchmarkState, i uint8) (bool, error) { hit(); return i == 1 || i == 2, nil },
		EqualInputs: func(a, b uint8) (bool, error) { hit(); return a == b, nil },
		SimplerInputs: func(input uint8) (Iterator[uint8], error) {
			hit()
			if input == 2 {
				return Values([]uint8{1}), nil
			}
			return Values([]uint8(nil)), nil
		},
	}
}
func benchmarkMetrics(b *testing.B, c *benchmarkCounters, s *Session[benchmarkState, uint8, uint32]) {
	n := float64(b.N)
	b.ReportMetric(float64(c.callbacks)/n, "callbacks/op")
	b.ReportMetric(float64(c.steps)/n, "steps/op")
	b.ReportMetric(float64(c.snapshots)/n, "snapshots/op")
	b.ReportMetric(float64(c.snapshotBytes)/n, "snapshot-bytes/op")
	b.ReportMetric(float64(c.encodedBytes)/n, "encoded-bytes/op")
	b.ReportMetric(float64(c.retainedRoots)/n, "retained-roots/op")
	b.ReportMetric(float64(len(s.roots)), "roots-at-end")
}
func BenchmarkSharedEngine(b *testing.B) {
	for _, fixture := range benchmarkFixtures {
		for _, operation := range []string{"Observed", "BFS", "Record", "Replay", "Fuzz", "Shrink", "Ring"} {
			b.Run(fixture.name+"/"+operation, func(b *testing.B) {
				b.ReportAllocs()
				b.StopTimer()
				var counters benchmarkCounters
				model := benchmarkModel(fixture, operation == "Fuzz" || operation == "Shrink", &counters)
				err := WithSession(model, DefaultOptions(), func(session *Session[benchmarkState, uint8, uint32]) error {
					initial := benchmarkState{Payload: make([]byte, fixture.payload)}
					inputs := make([]uint8, 64)
					for i := range inputs {
						inputs[i] = uint8(i%2) + 1
					}
					fuzzConfig := FuzzConfig{Seed: 0, Cases: 1, Steps: 64, Transitions: 256, MutationPercent: 0}
					var artifact []byte
					var original *Result[uint8]
					if operation == "Replay" {
						recorded, err := session.Record(inputs, 64)
						if err != nil {
							return err
						}
						artifact = recorded.Artifact
						if err = recorded.Close(); err != nil {
							return err
						}
					}
					if operation == "Shrink" {
						failure, err := session.Fuzz(fuzzConfig)
						if err != nil {
							return err
						}
						if failure.Status != PropertyFailed {
							return fmt.Errorf("missing benchmark failure")
						}
						original = failure
						defer original.Close()
					}
					counters = benchmarkCounters{}
					b.ResetTimer()
					b.StartTimer()
					for i := 0; i < b.N; i++ {
						switch operation {
						case "Observed":
							after := benchmarkState{Value: 2, Payload: initial.Payload}
							result, err := session.CheckObserved(initial, 2, Accept(after, uint32(0)), 1, FullChecks())
							if err != nil {
								return err
							}
							counters.retainedRoots += uint64(len(session.roots))
							if err = result.Close(); err != nil {
								return err
							}
						case "BFS":
							result, err := session.Enumerate(Bounds{States: 64, Transitions: 256, Depth: 64})
							if err != nil {
								return err
							}
							if result.States != 32 || result.Transitions != 64 {
								return fmt.Errorf("unexpected benchmark graph")
							}
							counters.retainedRoots += uint64(len(session.roots))
							if err = result.Close(); err != nil {
								return err
							}
						case "Record":
							recorded, err := session.Record(inputs, 64)
							if err != nil {
								return err
							}
							counters.retainedRoots += uint64(len(session.roots))
							if err = recorded.Close(); err != nil {
								return err
							}
						case "Replay":
							replayed, err := session.Replay(artifact, false)
							if err != nil {
								return err
							}
							if replayed.Status != OK || replayed.StepsVerified != 64 {
								return fmt.Errorf("unexpected benchmark replay")
							}
							counters.retainedRoots += uint64(len(session.roots))
							if err = replayed.Close(); err != nil {
								return err
							}
						case "Fuzz":
							failure, err := session.Fuzz(fuzzConfig)
							if err != nil {
								return err
							}
							if failure.Status != PropertyFailed {
								return fmt.Errorf("missing benchmark failure")
							}
							counters.retainedRoots += uint64(len(session.roots))
							if err = failure.Close(); err != nil {
								return err
							}
						case "Shrink":
							shrunk, err := session.Shrink(original, ShrinkConfig{Attempts: 256, Transitions: 4096})
							if err != nil {
								return err
							}
							if !shrunk.ValidatedOriginal {
								return fmt.Errorf("benchmark original not validated")
							}
							counters.retainedRoots += uint64(len(session.roots))
							if err = shrunk.Close(); err != nil {
								return err
							}
						case "Ring":
							ring, err := session.NewRing(initial, 16, 16*1024*1024)
							if err != nil {
								return err
							}
							before := initial
							for _, input := range inputs {
								after := benchmarkState{Value: (before.Value + uint32(input)) % 32, Payload: before.Payload}
								result, err := ring.Observe(before, input, Accept(after, before.Value))
								if err != nil {
									_ = ring.Close()
									return err
								}
								if err = result.Close(); err != nil {
									return err
								}
								before = after
							}
							snapshot, err := ring.Snapshot()
							if err != nil {
								_ = ring.Close()
								return err
							}
							counters.retainedRoots += uint64(len(session.roots))
							if err = snapshot.Close(); err != nil {
								return err
							}
							if err = ring.Close(); err != nil {
								return err
							}
						}
					}
					b.StopTimer()
					if original != nil {
						if err := original.Close(); err != nil {
							return err
						}
					}
					benchmarkMetrics(b, &counters, session)
					if len(session.roots) != 0 {
						return fmt.Errorf("benchmark retained host roots after result cleanup")
					}
					return nil
				})
				if err != nil {
					b.Fatal(err)
				}
			})
		}
	}
}
