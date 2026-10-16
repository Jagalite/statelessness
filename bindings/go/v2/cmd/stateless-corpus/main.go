//go:build cgo

package main

import (
	"fmt"
	"github.com/Jagalite/statelessness/bindings/go/v2/conformance"
	"os"
)

func main() {
	if err := conformance.Run(os.Stdin, os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
