package debug

import (
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

func TestLiveCaptureParentsImposeNoBranchPolicy(t *testing.T) {
	trace := func(parameters ...[2]string) *s.AuditTrace {
		return &s.AuditTrace{Config: s.RunConfig{Parameters: parameters}}
	}
	live := trace([2]string{"stateless.debug.origin", "recorded-live-observation"})
	if _, _, present, err := branchParentPolicy(live); err != nil || present {
		t.Fatal(present, err)
	}
	// Minimization keeps requiring complete simulation policy provenance.
	if _, _, _, err := recordedEnvironment(live); err == nil {
		t.Fatal("live capture accepted as simulation policy")
	}
	env, unrestricted, present, err := branchParentPolicy(trace([2]string{"stateless.debug.environment", "allowed"}, [2]string{"stateless.debug.origin", "simulation"}))
	if err != nil || !present || env != "allowed" || unrestricted {
		t.Fatal(env, unrestricted, present, err)
	}
	if _, _, _, err := branchParentPolicy(trace([2]string{"stateless.debug.origin", "simulation"})); err == nil {
		t.Fatal("partial simulation provenance accepted")
	}
}
