package statelessness

import "testing"

func TestCloneInputsFreezesSliceSlotsBeforeHooks(t *testing.T) {
	original := []int{1, 2}
	g := GenerateModel[int, int, int]{Model: Model[int, int, int]{
		CloneInput: func(v int) int {
			if v == 1 {
				original[1] = 99
			}
			return v
		},
	}}
	got, err := cloneInputs(g, original)
	if err != nil || len(got) != 2 || got[0] != 1 || got[1] != 2 {
		t.Fatalf("snapshot = %v, error = %v", got, err)
	}
}
