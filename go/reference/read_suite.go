package reference

import (
	"fmt"
	"io"
	"os"
	"path/filepath"

	s "github.com/Jagalite/statelessness/go"
)

// ReadMutationSuite reads a bounded set of local regular files, then verifies
// their identity and exactly replays all claimed fault witnesses. Platform
// implementations without safe nonblocking, no-follow admission fail closed.
// Callers must prevent replacement or renaming of the directory during this
// operation: entries are opened by pathname after directory enumeration, so
// the directory itself is not a stable capability for subsequent opens.
// Windows admission accepts fixed, removable, or RAM drive paths and excludes
// device, UNC, and mapped network drives. It rejects reparse points at the
// final component of each open. Callers must ensure intermediate components
// resolve to suitable local storage and do not change during loading: a local
// drive can contain junctions or other mounts targeting remote storage. Regular
// filesystem operations are synchronous and can block even on local storage.
func ReadMutationSuite(directory string, maxBytes uint64) (SuiteAssessment, error) {
	return ReadMutationSuiteWithBuilds(directory, maxBytes, nil)
}

// ReadMutationSuiteWithBuilds permits explicitly pinned cross-build replay.
func ReadMutationSuiteWithBuilds(directory string, maxBytes uint64, policy *SuiteBuildPolicy) (SuiteAssessment, error) {
	var empty SuiteAssessment
	if maxBytes == 0 {
		return empty, &s.ModelError{Message: "reference suite byte limit"}
	}
	dir, err := openSuiteRegular(directory)
	if err != nil {
		return empty, err
	}
	info, err := dir.Stat()
	if err != nil {
		dir.Close()
		return empty, err
	}
	if !info.IsDir() {
		dir.Close()
		return empty, fmt.Errorf("reference suite is not a directory")
	}
	entries, err := dir.ReadDir(2*len(mutationOrder) + 2)
	closeErr := dir.Close()
	if err != nil && err != io.EOF {
		return empty, err
	}
	if closeErr != nil {
		return empty, closeErr
	}
	if len(entries) > 1+2*len(mutationOrder) {
		return empty, fmt.Errorf("reference suite entry limit")
	}
	allowed := map[string]bool{"mutations.json": true}
	for _, name := range mutationOrder {
		allowed[name+"-original.sttrace"] = true
		allowed[name+"-minimized.sttrace"] = true
	}
	files := make(map[string][]byte, len(entries))
	var total uint64
	for _, entry := range entries {
		name := entry.Name()
		if !allowed[name] {
			return empty, fmt.Errorf("unexpected mutation evidence artifact")
		}
		f, err := openSuiteRegular(filepath.Join(directory, name))
		if err != nil {
			return empty, err
		}
		data, err := readSuiteFile(f, maxBytes-total)
		closeErr := f.Close()
		if err != nil {
			return empty, err
		}
		if closeErr != nil {
			return empty, closeErr
		}
		total += uint64(len(data))
		files[name] = data
	}
	return VerifyMutationSuiteWithBuilds(files, maxBytes, policy)
}

func readSuiteFile(f *os.File, remaining uint64) ([]byte, error) {
	info, err := f.Stat()
	if err != nil {
		return nil, err
	}
	size := info.Size()
	if !info.Mode().IsRegular() || size < 0 || uint64(size) > remaining || uint64(size) > uint64(int(^uint(0)>>1)) {
		return nil, fmt.Errorf("invalid or oversized mutation evidence file")
	}
	data := make([]byte, int(size))
	if _, err = io.ReadFull(f, data); err != nil {
		return nil, err
	}
	var extra [1]byte
	n, err := f.Read(extra[:])
	if n != 0 || err != io.EOF {
		if err != nil && err != io.EOF {
			return nil, err
		}
		return nil, fmt.Errorf("mutation evidence file changed during read")
	}
	return data, nil
}
