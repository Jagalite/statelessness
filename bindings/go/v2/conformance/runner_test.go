//go:build cgo

package conformance

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

// Execute the golden corpus through the Rust-backed fixture runner, including
// depth-bound termination names and coherent recording error prefixes.
func TestPortableCorpus(t *testing.T) {
	data, err := os.ReadFile("../../../../conformance/corpus.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Cases []struct {
			ID       string          `json:"id"`
			Request  json.RawMessage `json:"request"`
			Expected json.RawMessage `json:"expected"`
		} `json:"cases"`
	}
	if err = json.Unmarshal(data, &corpus); err != nil {
		t.Fatal(err)
	}
	for _, entry := range corpus.Cases {
		t.Run(entry.ID, func(t *testing.T) {
			got, err := json.Marshal(Response(entry.Request))
			if err != nil {
				t.Fatal(err)
			}
			var actual, expected any
			if err = json.Unmarshal(got, &actual); err != nil {
				t.Fatal(err)
			}
			if err = json.Unmarshal(entry.Expected, &expected); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(actual, expected) {
				t.Fatalf("got %s; expected %s", got, entry.Expected)
			}
		})
	}
}
