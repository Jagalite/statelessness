package statelessness

import "testing"

func TestDomainContainsRetainsQueryAcrossComparisons(t *testing.T) {
	clone := func(p *int) *int { v := *p; return &v }
	d, err := NewDomain(DomainDescriptor{MaxEntries: 2}, clone)
	if err != nil {
		t.Fatal(err)
	}
	a, b, query := 1, 2, 2
	if err := d.Push(&a); err != nil {
		t.Fatal(err)
	}
	if err := d.Push(&b); err != nil {
		t.Fatal(err)
	}
	calls := 0
	found, err := d.Contains(&query, func(a, b *int) bool {
		calls++
		query = 3
		return *a == *b
	})
	if err != nil || !found || calls != 2 {
		t.Fatalf("found=%v calls=%d err=%v", found, calls, err)
	}
}

func TestEmptyDomainContainsDoesNotClone(t *testing.T) {
	d, err := NewDomain(DomainDescriptor{MaxEntries: 0}, func(int) int { panic("unexpected clone") })
	if err != nil {
		t.Fatal(err)
	}
	found, err := d.Contains(1, func(int, int) bool { panic("unexpected equality") })
	if err != nil || found {
		t.Fatalf("found=%v err=%v", found, err)
	}
}

func TestDomainContainsRetainsEntriesAcrossCallbackMutation(t *testing.T) {
	clone := func(p *int) *int { v := *p; return &v }
	d, err := NewDomain(DomainDescriptor{MaxEntries: 2}, clone)
	if err != nil {
		t.Fatal(err)
	}
	a, b, query := 1, 2, 2
	if err := d.Push(&a); err != nil {
		t.Fatal(err)
	}
	if err := d.Push(&b); err != nil {
		t.Fatal(err)
	}
	calls := 0
	found, err := d.Contains(&query, func(a, b *int) bool {
		calls++
		if calls == 1 {
			entries := d.IntoEntries()
			*entries[1] = 3
		}
		return *a == *b
	})
	if err != nil || !found || calls != 2 {
		t.Fatalf("found=%v calls=%d err=%v", found, calls, err)
	}
}

func TestDomainContainsDefersUnusedEntryCloneFailure(t *testing.T) {
	bad := false
	d, err := NewDomain(DomainDescriptor{MaxEntries: 2}, func(v int) int {
		if bad && v == 2 {
			panic("bad entry")
		}
		return v
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := d.Push(1); err != nil {
		t.Fatal(err)
	}
	if err := d.Push(2); err != nil {
		t.Fatal(err)
	}
	bad = true
	found, err := d.Contains(1, func(a, b int) bool { return a == b })
	if err != nil || !found {
		t.Fatalf("found=%v err=%v", found, err)
	}
}

func TestDomainEntriesCapturesSlotsBeforeCloneHooks(t *testing.T) {
	var domain *Domain[int]
	mutate := false
	var err error
	domain, err = NewDomain(DomainDescriptor{MaxEntries: 2}, func(v int) int {
		if mutate && v == 1 {
			consumed := domain.IntoEntries()
			consumed[1] = 99
		}
		return v
	})
	if err != nil {
		t.Fatal(err)
	}
	if err = domain.Push(1); err != nil {
		t.Fatal(err)
	}
	if err = domain.Push(2); err != nil {
		t.Fatal(err)
	}
	mutate = true
	got, err := domain.Entries()
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 || got[0] != 1 || got[1] != 2 {
		t.Fatalf("snapshot = %v", got)
	}
}
