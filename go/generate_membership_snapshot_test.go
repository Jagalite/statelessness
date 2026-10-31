package statelessness

import "testing"

func TestAutoMembershipCapturesQueryBeforeDomainCallbacks(t *testing.T) {
	query := 7
	model := Model[int, *int, int]{
		CloneState:  func(v int) int { query = 99; return v },
		CloneInput:  func(v *int) *int { x := *v; return &x },
		CloneOutput: ValueCopy[int],
		CheckState:  func(int) ([]Check, error) { return nil, nil },
		Inputs:      func(int) (Iterator[*int], error) { x := 7; return Values([]*int{&x}), nil },
	}
	auto, err := Auto64(model, func(a, b *int) (bool, error) { return *a == *b, nil }, AutoOptions64{2})
	if err != nil {
		t.Fatal(err)
	}
	found, err := auto.IsEnabled(0, &query)
	if err != nil || !found {
		t.Fatalf("found=%v err=%v", found, err)
	}
}

func TestAutoMembershipDefersQueryCopyFailureForEmptyDomain(t *testing.T) {
	model := Model[int, int, int]{
		CloneState: ValueCopy[int], CloneInput: func(int) int { panic("query copy") },
		CloneOutput: ValueCopy[int], CheckState: func(int) ([]Check, error) { return nil, nil },
		Inputs: func(int) (Iterator[int], error) { return Values([]int{}), nil },
	}
	auto, err := Auto64(model, func(a, b int) (bool, error) { return a == b, nil }, AutoOptions64{2})
	if err != nil {
		t.Fatal(err)
	}
	found, err := auto.IsEnabled(0, 1)
	if err != nil || found {
		t.Fatalf("found=%v err=%v", found, err)
	}
}

func TestEnabledCapturesInputBeforeStateClone(t *testing.T) {
	query := 7
	g := GenerateModel[int, *int, int]{Model: Model[int, *int, int]{
		CloneState: func(v int) int { query = 99; return v },
		CloneInput: func(v *int) *int { x := *v; return &x },
	}, IsEnabled: func(_ int, v *int) (bool, error) { return *v == 7, nil }}
	found, err := g.enabled(0, &query)
	if err != nil || !found {
		t.Fatalf("found=%v err=%v", found, err)
	}
}
