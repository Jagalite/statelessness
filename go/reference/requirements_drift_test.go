package reference

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

// The embedded catalog copies are part of the controller build identity, so
// they must stay byte-identical to the independently reviewed requirements.
func TestEmbeddedRequirementsMatchReviewedCatalog(t *testing.T) {
	dir := os.Getenv("STATELESS_REQUIREMENTS_DIR")
	if dir == "" {
		dir = filepath.Join("..", "..", "docs", "requirements")
	}
	for embedded, reviewed := range map[string]string{
		"controller-properties.txt":    "controller-properties.txt",
		"controller-witnesses.txt":     "controller-witnesses.txt",
		"controller-requirements.json": "controller.json",
	} {
		local, err := sourceFiles.ReadFile(embedded)
		if err != nil {
			t.Fatal(err)
		}
		want, err := os.ReadFile(filepath.Join(dir, reviewed))
		if err != nil {
			t.Fatalf("read reviewed %s: %v", reviewed, err)
		}
		if !bytes.Equal(local, want) {
			t.Errorf("go/reference/%s drifted from docs/requirements/%s", embedded, reviewed)
		}
	}
}
