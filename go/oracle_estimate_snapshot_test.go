package statelessness_test

import (
	"errors"
	"slices"
	"strings"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

func TestOracleEstimateCapturesHistoryBeforeModelCallback(t *testing.T) {
	history := []int{7}
	m := s.Model[int, int, int]{
		CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		CheckState:          func(int) ([]s.Check, error) { return nil, nil },
		EstimatedStateBytes: func(int) (*uint64, error) { history[0] = 99; n := uint64(3); return &n, nil },
	}
	o := s.Oracle[int, []int, int, int]{
		InitialState:        func() ([]int, error) { return nil, nil },
		CloneHistory:        slices.Clone[[]int],
		Advance:             func(h []int, _ int, _ []int, _ s.Disposition) ([]int, error) { return h, nil },
		CheckState:          func([]int, int) ([]s.Check, error) { return nil, nil },
		EstimatedStateBytes: func(h []int) (*uint64, error) { n := uint64(h[0]); return &n, nil },
	}
	wrapped, err := s.WithOracle(m, o)
	if err != nil {
		t.Fatal(err)
	}
	got, err := wrapped.EstimatedStateBytes(s.OracleState[int, []int]{Model: 1, Oracle: history})
	if err != nil {
		t.Fatal(err)
	}
	want := uint64(10) + s.OracleStateOverhead[int, []int]()
	if got == nil || *got != want {
		t.Fatalf("estimate = %v, want %d", got, want)
	}
	if history[0] != 99 {
		t.Fatal("model callback did not execute")
	}
}

func TestOracleEstimateShortCircuitsAfterCapturedHistory(t *testing.T) {
	sentinel := errors.New("estimate failed")
	for _, tc := range []struct {
		name     string
		estimate func(int) (*uint64, error)
		wantErr  error
	}{
		{"unknown", func(int) (*uint64, error) { return nil, nil }, nil},
		{"error", func(int) (*uint64, error) { return nil, sentinel }, sentinel},
	} {
		t.Run(tc.name, func(t *testing.T) {
			clones, calls := 0, 0
			m := s.Model[int, int, int]{CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], CheckState: func(int) ([]s.Check, error) { return nil, nil }, EstimatedStateBytes: tc.estimate}
			o := s.Oracle[int, int, int, int]{InitialState: func() (int, error) { return 0, nil }, CloneHistory: func(v int) int { clones++; panic("clone failed") }, Advance: func(h, _ int, _ []int, _ s.Disposition) (int, error) { return h, nil }, CheckState: func(int, int) ([]s.Check, error) { return nil, nil }, EstimatedStateBytes: func(int) (*uint64, error) { calls++; n := uint64(1); return &n, nil }}
			w, err := s.WithOracle(m, o)
			if err != nil {
				t.Fatal(err)
			}
			got, err := w.EstimatedStateBytes(s.OracleState[int, int]{})
			if got != nil || (tc.wantErr == nil && err != nil) || (tc.wantErr != nil && (err == nil || !strings.Contains(err.Error(), tc.wantErr.Error()))) || calls != 0 || clones != 1 {
				t.Fatalf("got %v, err %v, oracle calls %d, clones %d", got, err, calls, clones)
			}
		})
	}
}
