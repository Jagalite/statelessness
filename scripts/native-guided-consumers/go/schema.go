package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math/big"
	"slices"
	"strconv"

	sl "github.com/Jagalite/statelessness/go"
)

const corpusSchema = "stateless-guided-v2/1"

type corpus struct {
	Schema       string        `json:"schema"`
	SourceCommit string        `json:"source_commit"`
	Cases        []fixtureCase `json:"cases"`
}

// Expectations are deliberately absent: decoding cannot feed golden reports
// into fixture execution, replay, shrinking, or output normalization.
type fixtureCase struct {
	ID           string            `json:"id"`
	Mode         string            `json:"mode"`
	Config       fuzzConfig        `json:"config"`
	Guidance     sl.GuidanceConfig `json:"guidance"`
	Model        modelSpec         `json:"model"`
	Feedback     feedbackSpec      `json:"feedback"`
	Control      controlSpec       `json:"control"`
	ShrinkConfig *shrinkConfig     `json:"shrink_config"`
}

type fuzzConfig struct {
	Seed            string `json:"seed"`
	Cases           int    `json:"cases"`
	MaxSteps        int    `json:"max_steps"`
	MaxTransitions  uint64 `json:"max_transitions"`
	MutationPercent int    `json:"mutation_percent"`
}

func (c fuzzConfig) native() (sl.FuzzConfig, error) {
	seed, err := strconv.ParseUint(c.Seed, 10, 64)
	if err != nil || strconv.FormatUint(seed, 10) != c.Seed {
		return sl.FuzzConfig{}, fmt.Errorf("seed must be a canonical decimal u64 string")
	}
	return sl.FuzzConfig{Seed: seed, Cases: c.Cases, MaxSteps: c.MaxSteps,
		MaxTransitions: c.MaxTransitions, MutationPercent: c.MutationPercent}, nil
}

type modelSpec struct {
	Kind        string           `json:"kind"`
	Generate    [][]*int64       `json:"generate"`
	Fallback    *int64           `json:"fallback"`
	Draws       int              `json:"draws"`
	RNGMod      *uint64          `json:"rng_mod"`
	Enabled     string           `json:"enabled"`
	Disposition *wireDisposition `json:"disposition"`
	Checks      []checkRule      `json:"checks"`
	FailTotal   *int64           `json:"fail_total"`
	Fault       *faultSpec       `json:"fault"`
}

type selector struct {
	Case  *int   `json:"case"`
	Depth *int64 `json:"depth"`
}

func (s selector) matches(caseNumber int, depth int64) bool {
	return (s.Case == nil || *s.Case == caseNumber) && (s.Depth == nil || *s.Depth == depth)
}

type checkRule struct {
	selector
	Phase  string      `json:"phase"`
	Checks []wireCheck `json:"checks"`
	Error  *string     `json:"error"`
}

type faultSpec struct {
	Callback string `json:"callback"`
	Call     int    `json:"call"`
	Message  string `json:"message"`
}

type feedbackSpec struct {
	Kind          string        `json:"kind"`
	Project       int           `json:"project"`
	Hash          string        `json:"hash"`
	Initial       []feature     `json:"initial"`
	InitialError  *string       `json:"initial_error"`
	Transitions   []feedbackRow `json:"transitions"`
	IgnoreOverrun bool          `json:"ignore_overrun"`
}

type feedbackRow struct {
	selector
	Keys  []feature `json:"keys"`
	Error *string   `json:"error"`
}

type controlSpec struct {
	Cancelled    bool          `json:"cancelled"`
	ZeroDuration bool          `json:"zero_duration"`
	CancelAfter  *callbackCall `json:"cancel_after"`
}

type callbackCall struct {
	Callback string `json:"callback"`
	Call     int    `json:"call"`
}

type shrinkConfig struct {
	MaxAttempts int `json:"max_attempts"`
}

// JSON numbers retain their exact decimal value, including values beyond f64's
// integer precision. The canonical rational identity also equates 1 and 1.0.
type feature struct {
	kind     byte
	identity string
	wire     json.RawMessage
	integers []int64
}

func (k *feature) UnmarshalJSON(data []byte) error {
	var value any
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err := decoder.Decode(&value); err != nil {
		return err
	}
	*k = feature{wire: slices.Clone(data)}
	switch v := value.(type) {
	case string:
		k.kind, k.identity = 's', v
	case json.Number:
		rational, ok := new(big.Rat).SetString(string(v))
		if !ok {
			return fmt.Errorf("invalid numeric feature %q", v)
		}
		k.kind, k.identity = 'n', rational.RatString()
	case []any:
		k.kind = 'a'
		k.integers = make([]int64, len(v))
		for i, element := range v {
			number, ok := element.(json.Number)
			if !ok {
				return fmt.Errorf("feature arrays must contain integers")
			}
			n, err := number.Int64()
			if err != nil {
				return fmt.Errorf("feature array element: %w", err)
			}
			k.integers[i] = n
		}
	default:
		return fmt.Errorf("feature must be a string, number, or integer array")
	}
	return nil
}

func (k feature) MarshalJSON() ([]byte, error) { return slices.Clone(k.wire), nil }
func cloneFeature(k feature) feature {
	k.wire, k.integers = slices.Clone(k.wire), slices.Clone(k.integers)
	return k
}
func equalFeature(a, b feature) bool {
	return a.kind == b.kind && a.identity == b.identity && slices.Equal(a.integers, b.integers)
}

func readCorpus(reader io.Reader) (*corpus, error) {
	var c corpus
	decoder := json.NewDecoder(reader)
	if err := decoder.Decode(&c); err != nil {
		return nil, err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return nil, fmt.Errorf("trailing JSON value after corpus")
		}
		return nil, err
	}
	if c.Schema != corpusSchema {
		return nil, fmt.Errorf("unsupported corpus schema %q", c.Schema)
	}
	ids := make(map[string]bool)
	for _, row := range c.Cases {
		if row.ID == "" || ids[row.ID] {
			return nil, fmt.Errorf("missing or duplicate case id %q", row.ID)
		}
		ids[row.ID] = true
		if err := row.validate(); err != nil {
			return nil, fmt.Errorf("case %s: %w", row.ID, err)
		}
	}
	return &c, nil
}

func (c fixtureCase) validate() error {
	if _, err := c.Config.native(); err != nil {
		return err
	}
	if c.Mode != "guided" && c.Mode != "ordinary" {
		return fmt.Errorf("unknown mode %q", c.Mode)
	}
	if c.Model.Kind != "table-walk-v1" {
		return fmt.Errorf("unknown model %q", c.Model.Kind)
	}
	if _, err := c.Model.Disposition.native(); err != nil {
		return err
	}
	if c.Model.Enabled != "nonnegative" && c.Model.Enabled != "successor" {
		return fmt.Errorf("unknown enabled rule %q", c.Model.Enabled)
	}
	if c.Model.Draws < 0 || (c.Model.RNGMod != nil &&
		(c.Model.Draws == 0 || *c.Model.RNGMod == 0 || *c.Model.RNGMod > uint64(1<<63-1))) {
		return fmt.Errorf("invalid generator draws/rng_mod")
	}
	if c.Feedback.Kind != "custom" && c.Feedback.Kind != "state" && c.Feedback.Kind != "exact" {
		return fmt.Errorf("unknown feedback kind %q", c.Feedback.Kind)
	}
	if c.Feedback.Hash != "constant" && c.Feedback.Hash != "value" {
		return fmt.Errorf("unknown feedback hash %q", c.Feedback.Hash)
	}
	if c.Feedback.Kind == "state" && c.Feedback.Project != 0 && c.Feedback.Project != 1 {
		return fmt.Errorf("state projection must be 0 or 1")
	}
	for _, rule := range c.Model.Checks {
		if rule.Phase != "InitialState" && rule.Phase != "State" && rule.Phase != "Transition" {
			return fmt.Errorf("unknown check phase %q", rule.Phase)
		}
		for _, check := range rule.Checks {
			if _, err := check.native(); err != nil {
				return err
			}
		}
	}
	if fault := c.Model.Fault; fault != nil {
		if fault.Call < 1 || !slices.Contains([]string{"initial_state", "generate", "is_enabled", "step"}, fault.Callback) {
			return fmt.Errorf("invalid fault selector")
		}
	}
	if cancel := c.Control.CancelAfter; cancel != nil {
		labels := []string{"metadata", "initial_state", "check_state", "check_transition", "observer_initial",
			"observer_transition", "observer_check_error", "initial_feedback", "transition_feedback", "generate", "is_enabled", "step"}
		if cancel.Call < 1 || !slices.Contains(labels, cancel.Callback) {
			return fmt.Errorf("invalid cancellation selector")
		}
	}
	// Guidance/fuzz limits are intentionally not prevalidated here: invalid
	// configurations must reach the public API and produce its observed error.
	return nil
}
