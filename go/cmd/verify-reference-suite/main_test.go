package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReadPinsRejectsDuplicateAndOversizedData(t *testing.T) {
	good := `{"report_build":"build","mutation_builds":{"one":"a"}}`
	p, err := readPins(strings.NewReader(good))
	if err != nil || p.ReportBuild != "build" || p.MutationBuilds["one"] != "a" {
		t.Fatal(p, err)
	}
	for _, bad := range []string{
		`{"report_build":"a","report_build":"b","mutation_builds":{}}`,
		`{"report_build":"a","mutation_builds":{"x":"a","x":"b"}}`,
		`{"report_build":"a","mutation_builds":{},"unknown":1}`,
		good + ` {}`,
		good + strings.Repeat(" ", 1<<20),
		"{\"report_build\":\"\xff\",\"mutation_builds\":{}}",
	} {
		if _, err = readPins(strings.NewReader(bad)); err == nil {
			t.Fatal("accepted invalid pin data")
		}
	}
}

func TestReadPinsFileRefusesNonRegularFiles(t *testing.T) {
	dir := t.TempDir()
	good := filepath.Join(dir, "pins.json")
	if err := os.WriteFile(good, []byte(`{"report_build":"build","mutation_builds":{}}`), 0600); err != nil {
		t.Fatal(err)
	}
	if p, err := readPinsFile(good); err != nil || p.ReportBuild != "build" {
		t.Fatal(p, err)
	}
	if _, err := readPinsFile(dir); err == nil {
		t.Fatal("accepted a directory as build pins")
	}
	link := filepath.Join(dir, "link.json")
	if err := os.Symlink(good, link); err == nil {
		if _, err = readPinsFile(link); err == nil {
			t.Fatal("accepted a symlink as build pins")
		}
	}
}
