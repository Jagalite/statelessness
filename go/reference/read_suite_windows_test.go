//go:build windows

package reference

import (
	"os"
	"path/filepath"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

func TestWindowsSuiteDriveTypeAdmission(t *testing.T) {
	for _, tc := range []struct {
		kind uintptr
		want bool
	}{{0, false}, {1, false}, {2, true}, {3, true}, {4, false}, {5, false}, {6, true}, {7, false}} {
		if got := admittedDriveType(tc.kind); got != tc.want {
			t.Fatalf("drive type %d: got %v", tc.kind, got)
		}
	}
}

func TestWindowsSuiteAdmission(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "suite")
	if _, err := WriteMutationSuite(dir, s.DefaultSearchConfig()); err != nil {
		t.Fatal(err)
	}
	result, err := ReadMutationSuite(dir, 64<<20)
	if err != nil || !result.Adequate {
		t.Fatal(result, err)
	}
	if _, err = ReadMutationSuite(dir, 10); err == nil {
		t.Fatal("ignored byte limit")
	}
	if f, err := openSuiteRegular(os.DevNull); err == nil {
		f.Close()
		t.Fatal("accepted device")
	}
	path := filepath.Join(dir, "missing-cleanup-original.sttrace")
	if err = os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err = os.Symlink(filepath.Join(dir, "mutations.json"), path); err != nil {
		t.Skipf("creating symlink unavailable: %v", err)
	}
	if _, err = ReadMutationSuite(dir, 64<<20); err == nil {
		t.Fatal("accepted reparse point")
	}
}
