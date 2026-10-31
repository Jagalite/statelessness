package statelessness_test

import (
	"encoding/hex"
	"encoding/json"
	"errors"
	s "github.com/Jagalite/statelessness/go"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

type encodeAction struct {
	Kind, Hex         string
	Actions           []encodeAction
	CallbackError     bool `json:"callback_error"`
	IgnoreChildErrors bool `json:"ignore_child_errors"`
}
type encodeResult struct {
	OK       bool           `json:"ok"`
	Children []encodeResult `json:"children"`
}
type encodeExpected struct {
	FinishOK   bool           `json:"finish_ok"`
	EncodedHex *string        `json:"encoded_hex"`
	Results    []encodeResult `json:"results"`
	Events     []string       `json:"events"`
}
type encodeVector struct {
	Case, Operation, Maximum, Mode, Encoder string
	HistoryMode                             string `json:"history_mode"`
	InitialHex                              string `json:"initial_hex"`
	Actions                                 []encodeAction
	Expected                                encodeExpected
}

func TestEncodeSharedCorpus(t *testing.T) {
	path := os.Getenv("STATELESS_ENCODE_CORPUS")
	if path == "" {
		path = "../conformance/encode-buffers-v1.json"
	}
	data, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct{ Cases []encodeVector }
	if e = json.Unmarshal(data, &corpus); e != nil {
		t.Fatal(e)
	}
	if len(corpus.Cases) < 46 {
		t.Fatal("missing encode vectors")
	}
	for _, v := range corpus.Cases {
		t.Run(v.Case, func(t *testing.T) {
			maximum, e := strconv.ParseUint(v.Maximum, 10, 64)
			if e != nil {
				t.Fatal(e)
			}
			bytes, e := hex.DecodeString(v.InitialHex)
			if e != nil {
				t.Fatal(e)
			}
			result := encodeExpected{Results: []encodeResult{}, Events: []string{}}
			makeModel := func(mode string) s.Model[int, int, int] {
				model := s.Model[int, int, int]{InitialState: func() (int, error) { return 0, nil }, Step: func(state, input int) (s.Transition[int, int], error) { return s.Accept[int, int](state + input), nil }, CheckState: func(int) ([]s.Check, error) { return nil, nil }, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], Inputs: func(int) (s.Iterator[int], error) { return s.Values([]int{1}), nil }, EqualStates: func(a, b int) bool { return a == b }, Codec: &s.Codec[int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("codec", "native-test"), nil }, DecodeState: func(data []byte) (int, error) { return strconv.Atoi(string(data)) }, DecodeInput: func(data []byte) (int, error) { return strconv.Atoi(string(data)) }}}
				legacy := func(kind string) func(int) ([]byte, error) {
					return func(value int) ([]byte, error) {
						if mode == "into" {
							return nil, errors.New("into codec used allocating legacy")
						}
						result.Events = append(result.Events, "legacy_"+kind)
						return []byte(strconv.Itoa(value)), nil
					}
				}
				into := func(kind string) func(int, *s.EncodeBuffer) error {
					return func(value int, buffer *s.EncodeBuffer) error {
						result.Events = append(result.Events, "into_"+kind)
						for _, digit := range []byte(strconv.Itoa(value)) {
							if e := buffer.Append([]byte{digit}); e != nil {
								return e
							}
						}
						return nil
					}
				}
				model.Codec.EncodeState = legacy("state")
				model.Codec.EncodeInput = legacy("input")
				model.Codec.EncodeOutput = legacy("output")
				if mode == "into" {
					model.Codec.EncodeStateInto = into("state")
					model.Codec.EncodeInputInto = into("input")
					model.Codec.EncodeOutputInto = into("output")
				}
				return model
			}
			makeOracle := func(mode string) s.Oracle[int, int, int, int] {
				oracle := s.Oracle[int, int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("history", "native-test"), nil }, InitialState: func() (int, error) { return 0, nil }, Advance: func(history, input int, _ []int, _ s.Disposition) (int, error) { return history + input, nil }, CheckState: func(int, int) ([]s.Check, error) { return nil, nil }, CloneHistory: s.ValueCopy[int], EqualHistory: func(a, b int) bool { return a == b }, DecodeHistory: func(data []byte) (int, error) { return strconv.Atoi(string(data)) }, EncodeHistory: func(value int) ([]byte, error) {
					if mode == "into" {
						return nil, errors.New("historyinto used legacy")
					}
					result.Events = append(result.Events, "legacy_history")
					return []byte(strconv.Itoa(value)), nil
				}}
				if mode == "into" {
					oracle.EncodeHistoryInto = func(value int, buffer *s.EncodeBuffer) error {
						result.Events = append(result.Events, "into_history")
						for _, digit := range []byte(strconv.Itoa(value)) {
							if e := buffer.Append([]byte{digit}); e != nil {
								return e
							}
						}
						return nil
					}
				}
				return oracle
			}
			e = s.WithEncodeBuffer(&bytes, maximum, func(buffer *s.EncodeBuffer) error {
				switch v.Operation {
				case "buffer":
					var run func(*s.EncodeBuffer, []encodeAction) ([]encodeResult, error)
					run = func(buffer *s.EncodeBuffer, actions []encodeAction) ([]encodeResult, error) {
						results := []encodeResult{}
						var first error
						for _, action := range actions {
							entry := encodeResult{Children: []encodeResult{}}
							var err error
							switch action.Kind {
							case "append", "legacy":
								chunk, e := hex.DecodeString(action.Hex)
								if e != nil {
									t.Fatal(e)
								}
								if action.Kind == "legacy" {
									err = buffer.ExtendFromVec(chunk)
								} else {
									err = buffer.Append(chunk)
								}
							case "framed":
								err = buffer.Framed(func(child *s.EncodeBuffer) error {
									var childError error
									entry.Children, childError = run(child, action.Actions)
									if action.CallbackError {
										return errors.New("fixture codec error")
									}
									if action.IgnoreChildErrors {
										return nil
									}
									return childError
								})
							default:
								t.Fatal("unknown buffer action")
							}
							entry.OK = err == nil
							results = append(results, entry)
							if err != nil && first == nil {
								first = err
							}
						}
						return results, first
					}
					result.Results, _ = run(buffer, v.Actions)
					return nil
				case "dispatch", "automatic":
					model := makeModel(v.Mode)
					if v.Operation == "automatic" {
						generated, e := s.Auto(model, func(a, b int) (bool, error) { return a == b, nil }, s.DefaultAutoOptions())
						if e != nil {
							return e
						}
						model = generated.Model
					}
					switch v.Encoder {
					case "state":
						return model.AppendStateEncoding(12, buffer)
					case "input":
						return model.AppendInputEncoding(12, buffer)
					case "output":
						return model.AppendOutputEncoding(12, buffer)
					default:
						t.Fatal("unknown encoder")
					}
				case "oracle", "nested", "paired":
					model := makeModel(v.Mode)
					oracle := makeOracle(v.HistoryMode)
					if v.Operation == "paired" {
						reference := makeModel("into")
						projection := s.Projection[int, int, int, int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("projection", "native-test"), nil }, ActualState: s.ValueCopy[int], ReferenceState: s.ValueCopy[int], ActualEffect: s.ValueCopy[int], ReferenceEffect: s.ValueCopy[int], EqualStates: func(a, b int) bool { return a == b }, EqualEffects: func(a, b int) bool { return a == b }}
						oracle, e = s.Paired(reference, projection)
						if e != nil {
							return e
						}
					}
					wrapped, e := s.WithOracle(model, oracle)
					if e != nil {
						return e
					}
					state := s.OracleState[int, int]{Model: 12, Oracle: 34}
					if v.Operation != "nested" {
						return wrapped.AppendStateEncoding(state, buffer)
					}
					outerOracle := s.Oracle[s.OracleState[int, int], int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("outer-history", "native-test"), nil }, InitialState: func() (int, error) { return 0, nil }, Advance: func(history, input int, _ []int, _ s.Disposition) (int, error) { return history + input, nil }, CheckState: func(int, s.OracleState[int, int]) ([]s.Check, error) { return nil, nil }, CloneHistory: s.ValueCopy[int], EncodeHistoryInto: oracle.EncodeHistoryInto, EncodeHistory: oracle.EncodeHistory, DecodeHistory: oracle.DecodeHistory}
					outer, e := s.WithOracle(wrapped.Model, outerOracle)
					if e != nil {
						return e
					}
					return outer.AppendStateEncoding(s.OracleState[s.OracleState[int, int], int]{Model: state, Oracle: 56}, buffer)
				default:
					t.Fatal("unknown encode operation")
				}
				return nil
			})
			result.FinishOK = e == nil
			if result.FinishOK {
				encoded := hex.EncodeToString(bytes)
				result.EncodedHex = &encoded
			}
			if !reflect.DeepEqual(result, v.Expected) {
				t.Fatalf("result%+v expected%+v error%v", result, v.Expected, e)
			}
		})
	}
}
func assertEncodeExpired(t *testing.T, buffer *s.EncodeBuffer) {
	t.Helper()
	operations := []func() error{func() error { return buffer.Append([]byte{1}) }, func() error { return buffer.ExtendFromVec(nil) }, func() error { return buffer.Framed(func(*s.EncodeBuffer) error { return nil }) }, func() error { _, e := buffer.Len(); return e }, func() error { _, e := buffer.Snapshot(); return e }, buffer.Finish}
	for _, operation := range operations {
		if e := operation(); e == nil || !strings.Contains(e.Error(), "expired") {
			t.Fatal("encode lease did not expire", e)
		}
	}
}
func TestEncodeBufferLeasesStickyOwnershipAndReuse(t *testing.T) {
	bytes := make([]byte, 3, 64)
	original := []byte{1, 2}
	var escaped, childEscaped, copied *s.EncodeBuffer
	e := s.WithEncodeBuffer(&bytes, 12, func(buffer *s.EncodeBuffer) error {
		escaped = buffer
		copy := *buffer
		copied = &copy
		if e := buffer.Append(original); e != nil {
			return e
		}
		original[0] = 99
		snapshot, e := buffer.Snapshot()
		if e != nil {
			return e
		}
		snapshot[0] = 99
		if e = buffer.Framed(func(child *s.EncodeBuffer) error { childEscaped = child; return child.Append([]byte{3}) }); e != nil {
			return e
		}
		assertEncodeExpired(t, childEscaped)
		return buffer.Append([]byte{4})
	})
	if e != nil || hex.EncodeToString(bytes) != "010201000000000000000304" || cap(bytes) != 64 {
		t.Fatal(bytes, e, cap(bytes))
	}
	assertEncodeExpired(t, escaped)
	assertEncodeExpired(t, copied)
	for _, mode := range []string{"error", "panic", "ignored-limit", "framed-error"} {
		t.Run(mode, func(t *testing.T) {
			e := s.WithEncodeBuffer(&bytes, 1, func(buffer *s.EncodeBuffer) error {
				escaped = buffer
				switch mode {
				case "error":
					return errors.New("codec error")
				case "panic":
					panic("codec panic")
				case "ignored-limit":
					_ = buffer.Append([]byte{1, 2})
					if snapshot, e := buffer.Snapshot(); e == nil || snapshot != nil {
						t.Fatal("failed bytes published")
					}
				case "framed-error":
					_ = buffer.Framed(func(*s.EncodeBuffer) error { return nil })
				}
				return nil
			})
			if e == nil {
				t.Fatal("failure became successful encoding")
			}
			assertEncodeExpired(t, escaped)
			if e = s.WithEncodeBuffer(&bytes, 1, func(fresh *s.EncodeBuffer) error { assertEncodeExpired(t, escaped); return fresh.Append([]byte{9}) }); e != nil || !reflect.DeepEqual(bytes, []byte{9}) {
				t.Fatal(bytes, e)
			}
		})
	}
}

func TestEveryPortableRecordReplayEncodingPrefersInto(t *testing.T) {
	model := counter()
	events := []string{}
	model.Codec.EncodeState = nil
	model.Codec.EncodeInput = nil
	model.Codec.EncodeOutput = nil
	model.Codec.EncodeStateInto = func(state counterState, buffer *s.EncodeBuffer) error {
		events = append(events, "state:"+strconv.Itoa(state.value))
		return buffer.Append([]byte(strconv.Itoa(state.value)))
	}
	model.Codec.EncodeInputInto = func(input string, buffer *s.EncodeBuffer) error {
		events = append(events, "input:"+input)
		return buffer.Append([]byte(input))
	}
	model.Codec.EncodeOutputInto = func(output string, buffer *s.EncodeBuffer) error {
		events = append(events, "output:"+output)
		return buffer.Append([]byte(output))
	}
	trace, e := s.Record(model, s.Values([]string{"increment", "increment", "increment"}), 10)
	if e != nil || trace.Termination != "property_failed" {
		t.Fatal(trace, e)
	}
	expected := []string{"state:0", "input:increment", "output:changed", "state:1", "input:increment", "output:changed", "state:2", "input:increment", "output:changed", "state:3"}
	if !reflect.DeepEqual(events, expected) {
		t.Fatal(events)
	}
	events = []string{}
	report, e := s.Replay(model, trace, false)
	if e != nil || report.Outcome != "exact" || !reflect.DeepEqual(events, expected) {
		t.Fatal(report, e, events)
	}
	oracle := s.Oracle[counterState, int, string, string]{Metadata: func() (s.Metadata, error) { return s.Identity("history", "test"), nil }, InitialState: func() (int, error) { return 0, nil }, Advance: func(h int, _ string, _ []string, _ s.Disposition) (int, error) { return h + 1, nil }, CheckState: func(int, counterState) ([]s.Check, error) { return nil, nil }, CloneHistory: s.ValueCopy[int], EncodeHistoryInto: func(h int, buffer *s.EncodeBuffer) error { return buffer.Append([]byte(strconv.Itoa(h))) }, DecodeHistory: func(data []byte) (int, error) { return strconv.Atoi(string(data)) }}
	wrapped, e := s.WithOracle(model, oracle)
	if e != nil {
		t.Fatal(e)
	}
	wrapped.Codec.EncodeStateInto = nil
	wrapped.Codec.EncodeInputInto = nil
	wrapped.Codec.EncodeOutputInto = nil
	trace, e = s.Record(wrapped.Model, s.Values([]string{"increment"}), 10)
	if e != nil {
		t.Fatal("nil wrapper hooks caused recursion/fallback failure", e)
	}
	report, e = s.Replay(wrapped.Model, trace, false)
	if e != nil || report.Outcome != "exact" {
		t.Fatal(report, e)
	}
}
