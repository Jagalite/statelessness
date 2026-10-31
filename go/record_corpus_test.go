package statelessness_test

import (
	"encoding/json"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"os"
	"strconv"
	"strings"
	"testing"
)

func TestRecordAuditSharedCorpus(t *testing.T) {
	path := os.Getenv("STATELESS_REPLAY_CORPUS")
	if path == "" {
		path = "../conformance/replay-evidence-v1.json"
	}
	data, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct {
		Cases []struct {
			Case          string
			Inputs        []int
			Config        s.RunConfig
			Limits        map[string]uint64
			ErrorAt       *string `json:"error_at"`
			BudgetFormula *string `json:"budget_formula"`
			Expected      struct {
				Error       bool
				Termination *string
				Steps       *int
			}
		} `json:"record_cases"`
	}
	if e = json.Unmarshal(data, &corpus); e != nil {
		t.Fatal(e)
	}
	if len(corpus.Cases) == 0 {
		t.Fatal("empty recording corpus")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Case, func(t *testing.T) {
			event := func(name string) error {
				if c.ErrorAt != nil && *c.ErrorAt == name {
					return fmt.Errorf("fixture codec replay error")
				}
				return nil
			}
			encode := func(kind string, v int, b *s.EncodeBuffer) error {
				if e := event(fmt.Sprintf("encode_%s:%d", kind, v)); e != nil {
					return e
				}
				return b.Append([]byte(strconv.Itoa(v)))
			}
			legacy := func(int) ([]byte, error) { t.Fatal("legacy encoder selected"); return nil, nil }
			m := s.Model[int, int, int]{InitialState: func() (int, error) { return 0, nil }, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], Step: func(st, in int) (s.Transition[int, int], error) { return s.Accept(st+in, st+in), nil }, CheckState: func(int) ([]s.Check, error) { return []s.Check{s.Passed("ok")}, nil }, CheckTransition: func(int, int, s.Transition[int, int]) ([]s.Check, error) { return []s.Check{s.Passed("edge")}, nil }}
			m.Codec = &s.Codec[int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("codec-fixture", "candidate-v1"), nil }, EncodeState: legacy, EncodeInput: legacy, EncodeOutput: legacy, DecodeState: func(b []byte) (int, error) { return strconv.Atoi(string(b)) }, DecodeInput: func(b []byte) (int, error) { return strconv.Atoi(string(b)) }, EncodeStateInto: func(v int, b *s.EncodeBuffer) error { return encode("state", v, b) }, EncodeInputInto: func(v int, b *s.EncodeBuffer) error { return encode("input", v, b) }, EncodeOutputInto: func(v int, b *s.EncodeBuffer) error { return encode("output", v, b) }}
			limits := binaryLimits(c.Limits)
			if c.BudgetFormula != nil {
				// Header + RUN frame/body + two STEP frames + reserved error END.
				str := func(v string) uint64 { return 4 + uint64(len(v)) }
				run := str("codec-fixture") + 12 + str("candidate-v1") + str(c.Config.Strategy) + 1 + 4
				if c.Config.Seed != nil {
					run += 8
				}
				params := append([][2]string(nil), c.Config.Parameters...)
				params = append(params, [2]string{"stateless.engine.version", s.Version}, [2]string{"stateless.engine.build", s.NativeEngineBuild})
				for _, p := range params {
					run += str(p[0]) + str(p[1])
				}
				run += 5 + 4 + str("ok") + 1
				step := uint64(8+5+1+4+5+5+4) + str("ok") + 1 + str("edge") + 1
				limits.MaxTotalBytes = 12 + 9 + run + 2*(9+step) + 278
				if strings.HasSuffix(*c.BudgetFormula, "-1") {
					limits.MaxTotalBytes--
				}
			}
			trace, err := s.RecordAudit(m, s.Values(c.Inputs), c.Config, 100, limits)
			if (err != nil) != c.Expected.Error {
				t.Fatalf("error=%v expected error=%v", err, c.Expected.Error)
			}
			if c.Expected.Termination != nil && (trace == nil || trace.Termination != *c.Expected.Termination) {
				t.Fatalf("trace=%+v expected termination=%s", trace, *c.Expected.Termination)
			}
			if c.Expected.Steps != nil && (trace == nil || len(trace.Steps) != *c.Expected.Steps) {
				t.Fatalf("trace=%+v expected steps=%d", trace, *c.Expected.Steps)
			}
			if trace != nil {
				assertAuditWritable(t, trace, limits)
			}
		})
	}
}
