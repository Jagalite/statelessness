// Verify a locally stored reference mutation suite using explicitly supplied
// producer build pins. This verifies replay, not the authenticity of the pins.
package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"unicode/utf8"

	"github.com/Jagalite/statelessness/go/reference"
)

type buildPins struct {
	ReportBuild    string            `json:"report_build"`
	MutationBuilds map[string]string `json:"mutation_builds"`
}

func readPins(r io.Reader) (buildPins, error) {
	var pins buildPins
	data, err := io.ReadAll(io.LimitReader(r, (1<<20)+1))
	if err != nil {
		return pins, err
	}
	if len(data) > 1<<20 || !utf8.Valid(data) {
		return pins, fmt.Errorf("invalid or oversized build pin data")
	}
	d := json.NewDecoder(bytes.NewReader(data))
	expect := func(want json.Delim) error {
		token, err := d.Token()
		if err != nil {
			return err
		}
		if token != want {
			return fmt.Errorf("invalid build pin object")
		}
		return nil
	}
	if err = expect('{'); err != nil {
		return pins, err
	}
	seen := map[string]bool{}
	for d.More() {
		token, e := d.Token()
		if e != nil {
			return pins, e
		}
		key, ok := token.(string)
		if !ok || seen[key] {
			return pins, fmt.Errorf("duplicate or invalid build pin key")
		}
		seen[key] = true
		switch key {
		case "report_build":
			if err = d.Decode(&pins.ReportBuild); err != nil {
				return pins, err
			}
		case "mutation_builds":
			if err = expect('{'); err != nil {
				return pins, err
			}
			pins.MutationBuilds = map[string]string{}
			for d.More() {
				token, e = d.Token()
				if e != nil {
					return pins, e
				}
				mutation, ok := token.(string)
				if !ok {
					return pins, fmt.Errorf("invalid mutation build key")
				}
				if _, exists := pins.MutationBuilds[mutation]; exists {
					return pins, fmt.Errorf("duplicate mutation build key")
				}
				var build string
				if err = d.Decode(&build); err != nil {
					return pins, err
				}
				pins.MutationBuilds[mutation] = build
				if len(pins.MutationBuilds) > 9 {
					return pins, fmt.Errorf("too many mutation build pins")
				}
			}
			if err = expect('}'); err != nil {
				return pins, err
			}
		default:
			return pins, fmt.Errorf("unknown build pin key")
		}
	}
	if err = expect('}'); err != nil {
		return pins, err
	}
	if !seen["report_build"] || !seen["mutation_builds"] {
		return pins, fmt.Errorf("missing build pin key")
	}
	if _, err = d.Token(); err != io.EOF {
		return pins, fmt.Errorf("invalid trailing build pin data")
	}
	return pins, nil
}

// readPinsFile refuses FIFOs, devices, directories, and symlinks so a pin path
// cannot block or substitute a non-file source. The opened descriptor is checked
// too, which closes the swap window between Lstat and Open.
func readPinsFile(path string) (buildPins, error) {
	var pins buildPins
	info, err := os.Lstat(path)
	if err != nil {
		return pins, err
	}
	if !info.Mode().IsRegular() {
		return pins, fmt.Errorf("build pins must be a regular file")
	}
	f, err := os.Open(path)
	if err != nil {
		return pins, err
	}
	defer f.Close()
	opened, err := f.Stat()
	if err != nil {
		return pins, err
	}
	if !opened.Mode().IsRegular() || !os.SameFile(info, opened) {
		return pins, fmt.Errorf("build pins must be a regular file")
	}
	return readPins(f)
}

func run() error {
	directory := flag.String("suite", "", "mutation suite directory")
	policyPath := flag.String("pins", "", "JSON file with expected report and mutation builds; omit for local-only")
	maxBytes := flag.Uint64("max-bytes", 64<<20, "maximum total suite bytes")
	flag.Parse()
	if *directory == "" {
		return fmt.Errorf("-suite is required")
	}
	var policy *reference.SuiteBuildPolicy
	if *policyPath != "" {
		pins, err := readPinsFile(*policyPath)
		if err != nil {
			return err
		}
		policy = &reference.SuiteBuildPolicy{ReportBuild: pins.ReportBuild, MutationBuilds: pins.MutationBuilds}
	}
	result, err := reference.ReadMutationSuiteWithBuilds(*directory, *maxBytes, policy)
	if err != nil {
		return err
	}
	if err = json.NewEncoder(os.Stdout).Encode(result); err != nil {
		return err
	}
	if !result.Adequate {
		return fmt.Errorf("reference suite is incomplete or did not kill every control")
	}
	return nil
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
