//go:build !darwin && !linux && !windows

package reference

import (
	"fmt"
	"os"
)

func openSuiteRegular(string) (*os.File, error) {
	return nil, fmt.Errorf("safe reference suite file admission is unavailable on this platform")
}
