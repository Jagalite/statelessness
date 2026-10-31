//go:build windows

package reference

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"unsafe"
)

var getDriveType = syscall.NewLazyDLL("kernel32.dll").NewProc("GetDriveTypeW")

// DRIVE_REMOVABLE, DRIVE_FIXED, and DRIVE_RAMDISK; unknown, absent, optical,
// and remote drives fail closed before CreateFile.
func admittedDriveType(kind uintptr) bool { return kind == 2 || kind == 3 || kind == 6 }

// Open the final component itself and reject reparse points and non-disk
// handles before any read. Filesystem opens and regular reads are synchronous
// and may block; no read is attempted on a pipe or device handle.
func openSuiteRegular(path string) (*os.File, error) {
	// Restrict this implementation to local DOS paths. Device namespaces and
	// remote named-pipe paths are not suitable for synchronous file admission.
	absolute, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	volume := filepath.VolumeName(absolute)
	if len(volume) != 2 || volume[1] != ':' || !((volume[0] >= 'A' && volume[0] <= 'Z') || (volume[0] >= 'a' && volume[0] <= 'z')) {
		return nil, fmt.Errorf("reference suite requires a local drive path")
	}
	root, err := syscall.UTF16PtrFromString(volume + `\`)
	if err != nil {
		return nil, err
	}
	kind, _, _ := getDriveType.Call(uintptr(unsafe.Pointer(root)))
	if !admittedDriveType(kind) {
		return nil, fmt.Errorf("reference suite drive type is not supported")
	}
	for _, segment := range strings.Split(absolute[len(volume):], `\`) {
		base := strings.ToUpper(strings.TrimRight(segment, ". "))
		if i := strings.IndexByte(base, '.'); i >= 0 {
			base = base[:i]
		}
		if base == "CON" || base == "PRN" || base == "AUX" || base == "NUL" || base == "CONIN$" || base == "CONOUT$" {
			return nil, fmt.Errorf("reference suite device path")
		}
		if strings.HasPrefix(base, "COM") || strings.HasPrefix(base, "LPT") {
			n := base[3:]
			if len(n) == 1 && n[0] >= '1' && n[0] <= '9' || n == "¹" || n == "²" || n == "³" {
				return nil, fmt.Errorf("reference suite device path")
			}
		}
	}
	name, err := syscall.UTF16PtrFromString(absolute)
	if err != nil {
		return nil, err
	}
	h, err := syscall.CreateFile(name, syscall.GENERIC_READ, syscall.FILE_SHARE_READ|syscall.FILE_SHARE_WRITE|syscall.FILE_SHARE_DELETE, nil, syscall.OPEN_EXISTING, syscall.FILE_FLAG_OPEN_REPARSE_POINT|syscall.FILE_FLAG_BACKUP_SEMANTICS, 0)
	if err != nil {
		return nil, err
	}
	closeOnError := func(e error) (*os.File, error) { syscall.CloseHandle(h); return nil, e }
	typ, err := syscall.GetFileType(h)
	if err != nil {
		return closeOnError(err)
	}
	if typ != syscall.FILE_TYPE_DISK {
		return closeOnError(fmt.Errorf("reference suite entry is not a disk file"))
	}
	var info syscall.ByHandleFileInformation
	if err = syscall.GetFileInformationByHandle(h, &info); err != nil {
		return closeOnError(err)
	}
	if info.FileAttributes&syscall.FILE_ATTRIBUTE_REPARSE_POINT != 0 {
		return closeOnError(fmt.Errorf("reference suite entry is a reparse point"))
	}
	f := os.NewFile(uintptr(h), path)
	if f == nil {
		return closeOnError(fmt.Errorf("invalid reference suite handle"))
	}
	return f, nil
}
