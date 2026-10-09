//go:build stateless_jobs

package main

/*
#include "../../../c/stateless.h"
#include <stdbool.h>
int32_t jobs_run(const StatelessModel*,const uint8_t*,size_t,_Bool,StatelessBuffer*,StatelessBuffer*,StatelessBuffer*);
*/
import "C"
import (
	"fmt"
	s "github.com/Jagalite/statelessness/bindings/go"
	"github.com/Jagalite/statelessness/bindings/go/jobs"
	"os"
	"unsafe"
)

var buildIdentity = "unqualified-local-build"

func bytes(b *C.StatelessBuffer) []byte {
	return C.GoBytes(unsafe.Pointer(C.stateless_buffer_data(b)), C.int(C.stateless_buffer_len(b)))
}
func run() error {
	if len(os.Args) < 3 {
		return fmt.Errorf("usage: jobs correct|faulty|replay-correct|replay-faulty PATH")
	}
	mode, path := os.Args[1], os.Args[2]
	faulty := mode == "faulty" || mode == "replay-faulty"
	replay := mode == "replay-correct" || mode == "replay-faulty"
	if mode != "correct" && mode != "faulty" && !replay {
		return fmt.Errorf("unknown mode")
	}
	var artifact []byte
	var err error
	if replay {
		artifact, err = os.ReadFile(path)
		if err != nil {
			return err
		}
	}
	return s.WithSession(jobs.Model{Faulty: faulty, Build: buildIdentity}, func(session *s.Session) error {
		r, o, a := C.stateless_buffer_new(0), C.stateless_buffer_new(0), C.stateless_buffer_new(0)
		defer C.stateless_buffer_free(r)
		defer C.stateless_buffer_free(o)
		defer C.stateless_buffer_free(a)
		if r == nil || o == nil || a == nil {
			return fmt.Errorf("native buffer allocation failed")
		}
		var p *C.uint8_t
		if len(artifact) > 0 {
			p = (*C.uint8_t)(unsafe.Pointer(&artifact[0]))
		}
		var status C.int32_t
		err := session.WithNative(func(handle unsafe.Pointer) error {
			status = C.jobs_run((*C.StatelessModel)(handle), p, C.size_t(len(artifact)), C.bool(replay), r, o, a)
			return nil
		})
		if err != nil {
			return err
		}
		report := bytes(r)
		fmt.Print(string(report))
		if !replay {
			if err := os.WriteFile(path+".report.txt", report, 0600); err != nil {
				return err
			}
		}
		expected := C.int32_t(0)
		if faulty {
			expected = 1
		}
		if status != expected {
			return fmt.Errorf("paired status %d; expected %d", status, expected)
		}
		if !replay && faulty {
			if err := os.WriteFile(path+".original.trace", bytes(o), 0600); err != nil {
				return err
			}
			return os.WriteFile(path+".reduced.trace", bytes(a), 0600)
		}
		return nil
	})
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
