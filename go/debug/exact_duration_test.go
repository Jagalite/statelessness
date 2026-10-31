package debug_test

import (
	"errors"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
)

func TestDebuggerExactDurationVerificationAndMinimization(t *testing.T) {
	viewer, err := d.NewEvidenceViewer(fixture(), s.DefaultEvidenceLimits())
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	g := model(&calls)
	zero := s.Uint128{}
	limits := s.ShrinkLimits{Control: s.RunLimits{MaxDurationNS: &zero}}
	if _, err = d.Verify(viewer, g.Model, limits); err == nil || !strings.Contains(err.Error(), "deadline") || calls != 0 {
		t.Fatal(err, calls)
	}
	if _, err = d.Minimize(viewer, g, 1, 0, s.DefaultShrinkConfig(), limits); err == nil || !strings.Contains(err.Error(), "deadline") || calls != 0 {
		t.Fatal(err, calls)
	}
	large := s.Uint128{Hi: 1}
	limits.Control.MaxDurationNS = &large
	old := g.Codec.Metadata
	g.Codec.Metadata = func() (s.Metadata, error) { large = s.Uint128{}; return old() }
	verified, err := d.Verify(viewer, g.Model, limits)
	if err != nil || verified.Report.Outcome != "exact" {
		t.Fatal(verified, err)
	}
	large = s.Uint128{Hi: 1}
	minimized, err := d.Minimize(viewer, g, 1, 0, s.DefaultShrinkConfig(), limits)
	if err != nil || !minimized.ValidatedOriginal {
		t.Fatal(minimized, err)
	}
}

func TestDebuggerPostReplayControlOnEarlyIncompatibleOutcome(t *testing.T) {
	viewer, err := d.NewEvidenceViewer(fixture(), s.DefaultEvidenceLimits())
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"deadline", "cancelled", "callback_error"} {
		t.Run(mode, func(t *testing.T) {
			calls := 0
			g := model(&calls)
			var cancelled atomic.Bool
			budget := s.Uint128{Hi: 1}
			if mode == "deadline" {
				budget = s.Uint128{Lo: 10_000_000}
			}
			g.Codec.Metadata = func() (s.Metadata, error) {
				if mode == "deadline" {
					time.Sleep(30 * time.Millisecond)
				} else {
					cancelled.Store(true)
				}
				if mode == "callback_error" {
					return s.Metadata{}, errors.New("metadata failed")
				}
				return s.Identity("incompatible", "build"), nil
			}
			result, err := d.Verify(viewer, g.Model, s.ShrinkLimits{Control: s.RunLimits{MaxDurationNS: &budget, Cancellation: &cancelled}})
			want := mode
			if mode == "callback_error" {
				want = "metadata failed"
			}
			if err == nil || !strings.Contains(err.Error(), want) || calls != 0 {
				t.Fatal(result, err, calls)
			}
			if mode != "callback_error" && (result == nil || result.Report == nil || result.Report.Outcome != "incompatible") {
				t.Fatal("replay evidence not retained", result)
			}
		})
	}
}

func TestDebuggerExactDeadlineChargesReplayCallbacks(t *testing.T) {
	viewer, err := d.NewEvidenceViewer(fixture(), s.DefaultEvidenceLimits())
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	g := model(&calls)
	old := g.Codec.Metadata
	g.Codec.Metadata = func() (s.Metadata, error) { time.Sleep(30 * time.Millisecond); return old() }
	budget := s.Uint128{Lo: 10_000_000}
	limits := s.ShrinkLimits{Control: s.RunLimits{MaxDurationNS: &budget}}
	if _, err = d.Verify(viewer, g.Model, limits); err == nil || !strings.Contains(err.Error(), "deadline") {
		t.Fatal("verification ignored callback time", err)
	}
	if _, err = d.Minimize(viewer, g, 1, 0, s.DefaultShrinkConfig(), limits); err == nil || !strings.Contains(err.Error(), "deadline") {
		t.Fatal("minimization ignored verification time", err)
	}
}
