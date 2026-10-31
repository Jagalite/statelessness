package reference

import (
	"crypto/sha256"
	"embed"
	"fmt"
)

//go:embed controller.go obligations.go identity.go codec.go coverage.go generation.go host.go evidence.go controller-witnesses.txt controller-properties.txt controller-requirements.json
var sourceFiles embed.FS

func controllerBuild(c Controller) string {
	h := sha256.New()
	for _, name := range []string{"controller.go", "obligations.go", "identity.go", "codec.go", "coverage.go", "generation.go", "host.go", "evidence.go", "controller-witnesses.txt", "controller-properties.txt", "controller-requirements.json"} {
		data, err := sourceFiles.ReadFile(name)
		if err != nil {
			panic(err)
		}
		h.Write([]byte(name))
		h.Write([]byte{0})
		h.Write(data)
	}
	return fmt.Sprintf("go-controller-v1-%x-g%d-a%d-t%d-m%q", h.Sum(nil), c.Bounds.Generations, c.Bounds.Attempts, c.Bounds.Time, c.Mutation)
}
