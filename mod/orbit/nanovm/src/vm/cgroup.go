package vm

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"syscall"
)

const cgRoot = "/sys/fs/cgroup"

func cgPath(name string) string { return filepath.Join(cgRoot, "nanovm", name) }

// cgroupSetup creates /sys/fs/cgroup/nanovm/<name>, applies the spec's caps,
// and returns an open fd for clone-into-cgroup (CLONE_INTO_CGROUP via Go's
// UseCgroupFD) so the child is born inside its limits — no post-start race.
// A box without cgroup v2 degrades to no caps rather than no vm: callers get
// (fd=-1, "") and should carry the warning to the user.
func cgroupSetup(s *Spec) (fd int, warn string, err error) {
	if _, err := os.Stat(filepath.Join(cgRoot, "cgroup.controllers")); err != nil {
		return -1, "cgroup v2 not mounted — mem/cpu/pids caps skipped", nil
	}
	// Delegate controllers down to our subtree. Best-effort: a controller a
	// parent never enabled just means that one cap silently has no teeth,
	// and we surface that below by checking the file we need exists.
	for _, p := range []string{cgRoot, filepath.Join(cgRoot, "nanovm")} {
		os.MkdirAll(p, 0o755)
		os.WriteFile(filepath.Join(p, "cgroup.subtree_control"), []byte("+memory +cpu +pids"), 0o644)
	}
	dir := cgPath(s.Name)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return -1, "", fmt.Errorf("cgroup create: %w", err)
	}
	var warns []string
	set := func(file, val string) {
		if err := os.WriteFile(filepath.Join(dir, file), []byte(val), 0o644); err != nil {
			warns = append(warns, fmt.Sprintf("%s=%s not applied", file, val))
		}
	}
	if s.Mem != "" {
		n, _ := ParseBytes(s.Mem)
		set("memory.max", fmt.Sprint(n))
	}
	if s.CPU > 0 {
		set("cpu.max", fmt.Sprintf("%d 100000", int(s.CPU*100000)))
	}
	if s.Pids > 0 {
		set("pids.max", fmt.Sprint(s.Pids))
	}
	// Raw syscall.Open, not os.Open: an *os.File would be garbage-collected
	// (and its fd closed) between here and the clone that consumes it.
	rawfd, err := syscall.Open(dir, syscall.O_RDONLY|syscall.O_DIRECTORY, 0)
	if err != nil {
		return -1, "", err
	}
	return rawfd, strings.Join(warns, "; "), nil
}

// cgroupRemove tears the slice down once the vm is dead.
func cgroupRemove(name string) error {
	dir := cgPath(name)
	if _, err := os.Stat(dir); os.IsNotExist(err) {
		return nil
	}
	return os.Remove(dir)
}
