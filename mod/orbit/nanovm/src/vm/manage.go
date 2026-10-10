package vm

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// Stop asks politely (SIGTERM), waits up to three seconds, then doesn't
// (SIGKILL). Killing pid 1 of the namespace takes the whole vm with it.
func Stop(name string) (*State, error) {
	st, err := Load(name)
	if err != nil {
		return nil, err
	}
	if st.Status == "running" {
		syscall.Kill(st.Pid, syscall.SIGTERM)
		for i := 0; i < 30; i++ {
			time.Sleep(100 * time.Millisecond)
			if st.refresh(); st.Status != "running" {
				break
			}
		}
		if st.Status == "running" {
			syscall.Kill(st.Pid, syscall.SIGKILL)
			time.Sleep(200 * time.Millisecond)
			st.refresh()
		}
		st.Status = "exited"
		st.save()
	}
	// Belt and braces: whatever the store believed, anything still alive in
	// the vm's cgroup dies before the slice is removed.
	if b, err := os.ReadFile(filepath.Join(cgPath(name), "cgroup.procs")); err == nil {
		for _, line := range strings.Fields(string(b)) {
			if pid, err := strconv.Atoi(line); err == nil {
				syscall.Kill(pid, syscall.SIGKILL)
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	cgroupRemove(name)
	return st, nil
}

// Remove stops the vm if needed and forgets it: state, log, cgroup, mount dir.
func Remove(name string) error {
	if _, err := Load(name); err != nil {
		return err
	}
	if _, err := Stop(name); err != nil {
		return err
	}
	os.Remove(statePath(name))
	os.Remove(LogPath(name))
	os.RemoveAll(filepath.Join("/run/nanovm", name))
	return cgroupRemove(name)
}

// Logs returns the last n lines of a detached vm's output.
func Logs(name string, n int) (string, error) {
	st, err := Load(name)
	if err != nil {
		return "", err
	}
	if st.LogFile == "" {
		return "", fmt.Errorf("vm %q ran in the foreground — its output went to the caller's terminal, not a log", name)
	}
	b, err := os.ReadFile(st.LogFile)
	if err != nil {
		return "", err
	}
	lines := strings.Split(strings.TrimRight(string(b), "\n"), "\n")
	if n > 0 && len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	return strings.Join(lines, "\n"), nil
}

// Exec runs a command inside a running vm's namespaces. The Go runtime is
// multithreaded before main(), and setns(CLONE_NEWNS) refuses multithreaded
// callers, so in-process entry is off the table without C — we delegate to
// util-linux nsenter, which is on effectively every Linux box. interactive
// wires the caller's terminal through; otherwise output is captured.
func Exec(name string, argv []string, interactive bool) (string, int, error) {
	st, err := Load(name)
	if err != nil {
		return "", -1, err
	}
	if st.Status != "running" {
		return "", -1, fmt.Errorf("vm %q is not running", name)
	}
	nsenter, err := exec.LookPath("nsenter")
	if err != nil {
		return "", -1, fmt.Errorf("exec needs util-linux nsenter on the host (setns from multithreaded Go is not possible)")
	}
	args := []string{"-t", strconv.Itoa(st.Pid), "-m", "-u", "-i", "-p"}
	if !st.Spec.Net {
		args = append(args, "-n")
	}
	args = append(append(args, "--"), argv...)
	cmd := exec.Command(nsenter, args...)
	if interactive {
		cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
		err = cmd.Run()
		return "", exitCode(cmd, err), nil
	}
	var buf bytes.Buffer
	cmd.Stdout, cmd.Stderr = &buf, &buf
	err = cmd.Run()
	return buf.String(), exitCode(cmd, err), nil
}

func exitCode(cmd *exec.Cmd, err error) int {
	if cmd.ProcessState != nil {
		return cmd.ProcessState.ExitCode()
	}
	if err != nil {
		return -1
	}
	return 0
}
