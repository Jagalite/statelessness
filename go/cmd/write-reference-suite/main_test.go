package main

import (
	"errors"
	"os"
	"path/filepath"
	"testing"

	s "github.com/Jagalite/statelessness/go"
	"github.com/Jagalite/statelessness/go/reference"
)

func TestIncompleteSuiteCannotPublishPins(t *testing.T) {
	path := filepath.Join(t.TempDir(), "pins.json")
	results := make([]reference.MutationResult, len(reference.Mutations()))
	for i := range results {
		results[i].Status = "killed"
	}
	results[1].Status = "not_evaluated"
	if err := produce("unused", path, func(string, s.SearchConfig) ([]reference.MutationResult, error) { return results, nil }); err == nil {
		t.Fatal("incomplete suite accepted")
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("pins unexpectedly exist", err)
	}
	if err := requireKilled(results[:1]); err == nil {
		t.Fatal("short result list accepted")
	}
}
func TestPinWriteReportsCleanupFailure(t *testing.T) {
	path := filepath.Join(t.TempDir(), "pins.json")
	writeFailure := errors.New("write failed")
	cleanupFailure := errors.New("cleanup failed")
	err := writePins(path, []byte("pins"), func(f *os.File, b []byte) (int, error) {
		_, e := f.Write(b[:1])
		if e != nil {
			return 0, e
		}
		return 1, writeFailure
	}, func(string) error { return cleanupFailure })
	if !errors.Is(err, writeFailure) || !errors.Is(err, cleanupFailure) {
		t.Fatalf("missing failure: %v", err)
	}
	if err = os.Remove(path); err != nil {
		t.Fatal(err)
	}
	err = writePins(path, []byte("pins"), func(f *os.File, b []byte) (int, error) { return f.Write(b[:1]) }, os.Remove)
	if err == nil {
		t.Fatal("short write accepted")
	}
	if _, err = os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("partial pin file retained", err)
	}
}
