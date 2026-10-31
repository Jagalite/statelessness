package statelessness

import (
	"reflect"
	"testing"
)

func TestDomainProductCapturesSlicesBeforeMapper(t *testing.T) {
	a, b := []int{1, 2}, []int{3, 4}
	got, err := DomainProduct(a, b, 4, func(x, y int) ([2]int, error) {
		a[1], b[1] = 20, 40
		return [2]int{x, y}, nil
	})
	if err != nil || !reflect.DeepEqual(got, [][2]int{{1, 3}, {1, 4}, {2, 3}, {2, 4}}) {
		t.Fatalf("product = %v, %v", got, err)
	}
}
