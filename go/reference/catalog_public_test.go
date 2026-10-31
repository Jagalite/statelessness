package reference_test

import (
	r "github.com/Jagalite/statelessness/go/reference"
	"testing"
)

func TestMutationCatalogIsDetachedFromAuthority(t *testing.T) {
	names := r.Mutations()
	if len(names) != 9 {
		t.Fatal(names)
	}
	target, ok := r.MutationTarget(names[0])
	if !ok || target != "controller.publication_current" {
		t.Fatal(target, ok)
	}
	names[0] = "forged"
	next := r.Mutations()
	if next[0] != "obsolete-publication" {
		t.Fatal("caller modified catalog")
	}
	if _, ok := r.MutationTarget("forged"); ok {
		t.Fatal("unknown mutation accepted")
	}
}
