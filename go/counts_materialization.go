package statelessness

import "unsafe"

// Logical Rust64 counts are never narrowed to allocate their configured bound.
// Check actual growth, including its byte-size multiplication, at append sites.
// Available RAM and runtime allocator overhead remain host resource constraints.
func countCanAppend[T any](length int) error {
	maximum := uint64(^uint(0) >> 1)
	next := uint64(length) + 1
	var value T
	size := uint64(unsafe.Sizeof(value))
	if next > maximum || (size != 0 && next > maximum/size) {
		return &ModelError{"materialized count exceeds native allocation range"}
	}
	return nil
}
