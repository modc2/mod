package vm

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"time"
)

const initArg = "__init" // hidden subcommand: this binary re-exec'd as the namespace init
const specEnv = "NANOVM_SPEC"

// Run boots a nanovm: clone this binary into fresh namespaces (and, when
// cgroup v2 is there, directly into its cgroup slice — no post-start race),
// where it re-enters as Child(), builds the root filesystem and execs the
// payload as pid 1. Foreground runs block until exit; Detach returns at once
// with stdout+stderr flowing to the vm's log file.
func Run(s Spec) (*State, error) {
	if err := s.Validate(); err != nil {
		return nil, err
	}
	if prev, err := Load(s.Name); err == nil && prev.Status == "running" {
		return nil, fmt.Errorf("vm %q is already running (pid %d) — stop it or pick a name", s.Name, prev.Pid)
	}
	self, err := os.Executable()
	if err != nil {
		return nil, err
	}
	specJSON, _ := json.Marshal(s)

	cgfd, warn, err := cgroupSetup(&s)
	if err != nil {
		return nil, err
	}
	if cgfd >= 0 {
		defer syscall.Close(cgfd)
	}

	flags := uintptr(syscall.CLONE_NEWUTS | syscall.CLONE_NEWPID | syscall.CLONE_NEWNS | syscall.CLONE_NEWIPC)
	if !s.Net {
		flags |= syscall.CLONE_NEWNET
	}
	cmd := exec.Command(self, initArg)
	// The liveness marker goes in at birth, not at payload exec — otherwise
	// there is a window where refresh() reads the child's environ, misses the
	// marker, and wrongly persists the vm as exited.
	cmd.Env = append(os.Environ(), specEnv+"="+string(specJSON), "NANOVM_NAME="+s.Name)
	attr := &syscall.SysProcAttr{Cloneflags: flags}
	if cgfd >= 0 {
		attr.UseCgroupFD, attr.CgroupFD = true, cgfd
	}
	cmd.SysProcAttr = attr

	st := &State{Spec: s, Status: "running", Started: time.Now(), Warning: warn}
	if s.Detach {
		if err := os.MkdirAll(logsDir(), 0o755); err != nil {
			return nil, err
		}
		lf, err := os.OpenFile(LogPath(s.Name), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
		if err != nil {
			return nil, err
		}
		defer lf.Close()
		cmd.Stdout, cmd.Stderr = lf, lf
		st.LogFile = LogPath(s.Name)
	} else {
		cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	}

	if err := cmd.Start(); err != nil {
		cgroupRemove(s.Name)
		return nil, fmt.Errorf("clone failed: %w (new namespaces need root or CAP_SYS_ADMIN)", err)
	}
	st.Pid = cmd.Process.Pid
	if err := st.save(); err != nil {
		return nil, err
	}
	if s.Detach {
		cmd.Process.Release()
		return st, nil
	}
	cmd.Wait()
	st.Status, st.ExitCode = "exited", cmd.ProcessState.ExitCode()
	st.save()
	cgroupRemove(s.Name)
	return st, nil
}

// IsInit reports whether this process is the re-exec'd namespace init.
func IsInit(args []string) bool { return len(args) > 1 && args[1] == initArg }

// Child runs inside the fresh namespaces: hostname, root filesystem, /proc,
// environment, then exec the payload so it becomes pid 1. Never returns.
func Child() {
	var s Spec
	if err := json.Unmarshal([]byte(os.Getenv(specEnv)), &s); err != nil || s.Name == "" {
		fatal(fmt.Errorf("no spec in %s — this subcommand is internal, use `nanovm run`", specEnv))
	}
	if err := syscall.Sethostname([]byte(s.Hostname)); err != nil {
		fatal(fmt.Errorf("sethostname: %w", err))
	}
	// Our mount ns starts as a mirror of the host's with shared propagation;
	// flip it private first or every bind below leaks back out.
	if err := syscall.Mount("", "/", "", syscall.MS_REC|syscall.MS_PRIVATE, ""); err != nil {
		fatal(fmt.Errorf("make / private: %w", err))
	}

	hostMode := s.Rootfs == ""
	var newroot string
	if hostMode {
		newroot = filepath.Join("/run/nanovm", s.Name, "root")
		must(os.MkdirAll(newroot, 0o755), "mkdir newroot")
		must(syscall.Mount("/", newroot, "", syscall.MS_BIND|syscall.MS_REC, ""), "bind host root")
		// rw islands first — they are separate mounts, so the read-only
		// remount of the top mount later never touches them.
		for _, p := range s.Writable {
			abs, _ := filepath.Abs(p)
			if err := syscall.Mount(abs, filepath.Join(newroot, abs), "", syscall.MS_BIND|syscall.MS_REC, ""); err != nil {
				fatal(fmt.Errorf("writable bind %s: %w", abs, err))
			}
		}
		must(syscall.Mount("tmpfs", filepath.Join(newroot, "tmp"), "tmpfs", 0, "mode=1777"), "tmpfs /tmp")
	} else {
		newroot, _ = filepath.Abs(s.Rootfs)
		// pivot_root wants a mount point, not just a directory
		must(syscall.Mount(newroot, newroot, "", syscall.MS_BIND|syscall.MS_REC, ""), "bind rootfs")
		if fi, err := os.Stat(filepath.Join(newroot, "dev")); err == nil && fi.IsDir() {
			syscall.Mount("/dev", filepath.Join(newroot, "dev"), "", syscall.MS_BIND|syscall.MS_REC, "")
		}
		if fi, err := os.Stat(filepath.Join(newroot, "tmp")); err == nil && fi.IsDir() {
			syscall.Mount("tmpfs", filepath.Join(newroot, "tmp"), "tmpfs", 0, "mode=1777")
		}
	}

	// pivot_root: put_old must be creatable, which on the soon-to-be-read-only
	// host bind means on the tmpfs we just mounted at /tmp.
	putOld := filepath.Join(newroot, "tmp", ".pivot_old")
	oldInside := "/tmp/.pivot_old"
	if !hostMode {
		if _, err := os.Stat(filepath.Join(newroot, "tmp")); err != nil {
			putOld, oldInside = filepath.Join(newroot, ".pivot_old"), "/.pivot_old"
		}
	}
	must(os.MkdirAll(putOld, 0o700), "mkdir put_old")
	must(syscall.PivotRoot(newroot, putOld), "pivot_root")
	must(os.Chdir("/"), "chdir /")
	must(syscall.Unmount(oldInside, syscall.MNT_DETACH), "detach old root")
	os.Remove(oldInside)

	if hostMode {
		// Top mount only: submounts (the rw binds, /tmp, /proc below) keep
		// their own flags. Best-effort — some hosts carry extra flags on /
		// that make a bare remount EPERM, and a rw lens beats no vm.
		syscall.Mount("", "/", "", syscall.MS_REMOUNT|syscall.MS_BIND|syscall.MS_RDONLY, "")
	}
	if fi, err := os.Stat("/proc"); err == nil && fi.IsDir() {
		must(syscall.Mount("proc", "/proc", "proc", 0, ""), "mount /proc")
	}

	env := []string{
		"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
		"HOME=/root", "TERM=xterm",
		"NANOVM_NAME=" + s.Name, // the liveness marker refresh() looks for
	}
	env = append(env, s.Env...)
	if s.Dir != "" {
		must(os.Chdir(s.Dir), "chdir "+s.Dir)
	}
	os.Setenv("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
	path, err := exec.LookPath(s.Cmd[0])
	if err != nil {
		fatal(fmt.Errorf("%q not found inside the vm (rootfs=%s)", s.Cmd[0], orHost(s.Rootfs)))
	}
	fatal(syscall.Exec(path, s.Cmd, env)) // only returns on error
}

func orHost(rootfs string) string {
	if rootfs == "" {
		return "host"
	}
	return rootfs
}

func must(err error, what string) {
	if err != nil {
		fatal(fmt.Errorf("%s: %w", what, err))
	}
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, "nanovm init:", err)
	os.Exit(111)
}
