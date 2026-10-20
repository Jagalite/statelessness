package statelessness

import (
	"errors"
	"strings"
	"testing"
)

type failingEvidenceFile struct {
	writeError, syncError, closeError error
	syncCalls, closeCalls             int
}

func (f *failingEvidenceFile) Write(b []byte) (int, error) {
	if f.writeError != nil {
		return 0, f.writeError
	}
	return len(b), nil
}
func (f *failingEvidenceFile) Sync() error  { f.syncCalls++; return f.syncError }
func (f *failingEvidenceFile) Close() error { f.closeCalls++; return f.closeError }
func TestEvidenceNewFileFailureReceipts(t *testing.T) {
	for _, stage := range []string{"write", "sync", "close"} {
		t.Run(stage, func(t *testing.T) {
			failure := errors.New(stage + " failed")
			file := &failingEvidenceFile{}
			switch stage {
			case "write":
				file.writeError = failure
			case "sync":
				file.syncError = failure
			case "close":
				file.closeError = failure
			}
			err := evidenceWriteNew(file, []byte("bounded bytes"))
			if !errors.Is(err, failure) || !strings.Contains(err.Error(), "incomplete newly created evidence file") || file.closeCalls != 1 {
				t.Fatalf("%v %+v", err, file)
			}
			if stage == "write" && file.syncCalls != 0 || stage != "write" && file.syncCalls != 1 {
				t.Fatal("wrong sync boundary")
			}
		})
	}
}
