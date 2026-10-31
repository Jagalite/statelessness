//go:build darwin || linux

package reference

import (
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"

	s "github.com/Jagalite/statelessness/go"
)

func TestReadMutationSuiteAdmissionAndReplay(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "suite")
	if _, err := WriteMutationSuite(dir, s.DefaultSearchConfig()); err != nil {
		t.Fatal(err)
	}
	assessment, err := ReadMutationSuite(dir, 64<<20)
	if err != nil || !assessment.Adequate {
		t.Fatal(assessment, err)
	}
	if _, err = ReadMutationSuite(dir, 10); err == nil {
		t.Fatal("ignored byte limit")
	}
	path := filepath.Join(dir, "missing-cleanup-original.sttrace")
	if err = os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err = os.Symlink(filepath.Join(dir, "mutations.json"), path); err != nil {
		t.Fatal(err)
	}
	if _, err = ReadMutationSuite(dir, 64<<20); err == nil {
		t.Fatal("accepted symlink")
	}
	if err = os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err = syscall.Mkfifo(path, 0600); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { _, e := ReadMutationSuite(dir, 64<<20); done <- e }()
	select {
	case err = <-done:
		if err == nil {
			t.Fatal("accepted FIFO")
		}
	case <-time.After(3 * time.Second):
		t.Fatal("FIFO blocked reader")
	}
}
