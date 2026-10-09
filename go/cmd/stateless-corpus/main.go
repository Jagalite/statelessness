package main

import (
	"fmt"
	"github.com/Jagalite/statelessness/go/conformance"
	"os"
)

func main() {
	if err := conformance.Run(os.Stdin, os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
