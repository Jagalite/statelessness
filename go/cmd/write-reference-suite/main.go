// Write an exclusively created reference suite and separate producer pins.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"

	s "github.com/Jagalite/statelessness/go"
	"github.com/Jagalite/statelessness/go/reference"
)

func run() error {
	suite := flag.String("suite", "", "new suite directory")
	pins := flag.String("pins", "", "new build pins file")
	flag.Parse()
	if *suite == "" || *pins == "" {
		return fmt.Errorf("-suite and -pins are required")
	}
	return produce(*suite, *pins, reference.WriteMutationSuite)
}

func produce(suite, pins string, generate func(string, s.SearchConfig) ([]reference.MutationResult, error)) error {
	results, err := generate(suite, s.DefaultSearchConfig())
	if err != nil {
		return err
	}
	if err = requireKilled(results); err != nil {
		return err
	}
	builds := map[string]string{}
	for _, name := range reference.Mutations() {
		model := reference.Controller{Bounds: reference.DefaultBounds(), Mutation: name}.Model()
		meta, e := model.Codec.Metadata()
		if e != nil {
			return e
		}
		builds[name] = meta.Build
	}
	model := reference.Controller{Bounds: reference.DefaultBounds()}.Model()
	metadata, err := model.Codec.Metadata()
	if err != nil {
		return err
	}
	data, err := json.Marshal(struct {
		ReportBuild    string            `json:"report_build"`
		MutationBuilds map[string]string `json:"mutation_builds"`
	}{metadata.Build, builds})
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return writePins(pins, data, func(f *os.File, data []byte) (int, error) { return f.Write(data) }, os.Remove)
}

func requireKilled(results []reference.MutationResult) error {
	if len(results) != len(reference.Mutations()) {
		return fmt.Errorf("mutation suite is incomplete")
	}
	for _, result := range results {
		if result.Status != "killed" {
			return fmt.Errorf("mutation suite is incomplete")
		}
	}
	return nil
}

func writePins(path string, data []byte, write func(*os.File, []byte) (int, error), remove func(string) error) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	written, err := write(f, data)
	if err == nil && written != len(data) {
		err = fmt.Errorf("short pin write")
	}
	if err == nil {
		err = f.Sync()
	}
	if closeErr := f.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return errors.Join(err, remove(path))
	}
	return nil
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
