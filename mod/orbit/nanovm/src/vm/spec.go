// Package vm is the nanovm framework: micro-VMs out of nothing but the Linux
// kernel. No daemon, no image format, no C, no dependencies — a nanovm is a
// process behind its own PID/UTS/mount/IPC (and optionally net) namespaces,
// its own root filesystem via pivot_root, and a cgroup v2 slice for memory,
// CPU and pid caps. The whole runtime is this package plus one re-exec of the
// binary itself as the namespace init.
package vm

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Spec is everything a nanovm is, declared up front. The zero value plus a
// Name and Cmd is a valid machine: host root read-only, fresh /proc, private
// /tmp, network shared with the host, no resource caps.
type Spec struct {
	Name     string   `json:"name"`
	Cmd      []string `json:"cmd"`
	Rootfs   string   `json:"rootfs,omitempty"`   // "" = host root, read-only. Else a dir to pivot_root into.
	Hostname string   `json:"hostname,omitempty"` // defaults to Name
	Mem      string   `json:"mem,omitempty"`      // "64M", "1G" → memory.max
	CPU      float64  `json:"cpu,omitempty"`      // cores, e.g. 0.5 → cpu.max "50000 100000"
	Pids     int      `json:"pids,omitempty"`     // pids.max
	Net      bool     `json:"net"`                // false = own (empty) netns: no network at all
	Writable []string `json:"writable,omitempty"` // host-root mode: host paths bind-mounted rw inside
	Env      []string `json:"env,omitempty"`      // KEY=val, appended to a minimal base
	Dir      string   `json:"dir,omitempty"`      // working directory inside the vm
	Detach   bool     `json:"detach,omitempty"`   // run in background, stdout/err → log file
}

// State is a Spec that happened.
type State struct {
	Spec     Spec      `json:"spec"`
	Pid      int       `json:"pid"`
	Status   string    `json:"status"` // running | exited
	ExitCode int       `json:"exit_code"`
	Started  time.Time `json:"started"`
	LogFile  string    `json:"log_file,omitempty"`
	Warning  string    `json:"warning,omitempty"` // degraded, not failed — e.g. caps skipped
}

var nameRe = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,63}$`)

// Validate rejects a spec before any kernel call does, with a better message.
func (s *Spec) Validate() error {
	if !nameRe.MatchString(s.Name) {
		return errors.New("name must be [a-z0-9][a-z0-9_-]{0,63}")
	}
	if len(s.Cmd) == 0 {
		return errors.New("cmd is required — what should pid 1 be?")
	}
	if s.Rootfs != "" {
		fi, err := os.Stat(s.Rootfs)
		if err != nil || !fi.IsDir() {
			return fmt.Errorf("rootfs %q is not a directory", s.Rootfs)
		}
	}
	if s.Mem != "" {
		if _, err := ParseBytes(s.Mem); err != nil {
			return err
		}
	}
	if s.Hostname == "" {
		s.Hostname = s.Name
	}
	return nil
}

// ParseBytes turns "64M" / "1G" / "512k" / plain bytes into a number.
func ParseBytes(s string) (int64, error) {
	s = strings.TrimSpace(s)
	mult := int64(1)
	switch {
	case strings.HasSuffix(strings.ToUpper(s), "K"):
		mult, s = 1<<10, s[:len(s)-1]
	case strings.HasSuffix(strings.ToUpper(s), "M"):
		mult, s = 1<<20, s[:len(s)-1]
	case strings.HasSuffix(strings.ToUpper(s), "G"):
		mult, s = 1<<30, s[:len(s)-1]
	}
	n, err := strconv.ParseInt(strings.TrimSpace(s), 10, 64)
	if err != nil || n <= 0 {
		return 0, fmt.Errorf("bad size %q (want e.g. 64M, 1G)", s)
	}
	return n * mult, nil
}

// ── the store: one JSON file per vm under the data dir ────────────────────

// DataDir is where state and logs live. Overridable for tests via NANOVM_DATA.
func DataDir() string {
	if d := os.Getenv("NANOVM_DATA"); d != "" {
		return d
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".mod", "nanovm")
}

func vmsDir() string  { return filepath.Join(DataDir(), "vms") }
func logsDir() string { return filepath.Join(DataDir(), "logs") }

func statePath(name string) string { return filepath.Join(vmsDir(), name+".json") }

// LogPath is where a detached vm's stdout+stderr land.
func LogPath(name string) string { return filepath.Join(logsDir(), name+".log") }

func (st *State) save() error {
	if err := os.MkdirAll(vmsDir(), 0o755); err != nil {
		return err
	}
	b, _ := json.MarshalIndent(st, "", "  ")
	return os.WriteFile(statePath(st.Spec.Name), b, 0o644)
}

// Load reads one vm's state and refreshes its liveness against /proc.
func Load(name string) (*State, error) {
	b, err := os.ReadFile(statePath(name))
	if err != nil {
		return nil, fmt.Errorf("no vm named %q", name)
	}
	st := &State{}
	if err := json.Unmarshal(b, st); err != nil {
		return nil, err
	}
	st.refresh()
	return st, nil
}

// List returns every known vm, liveness refreshed.
func List() ([]*State, error) {
	entries, err := os.ReadDir(vmsDir())
	if err != nil {
		if os.IsNotExist(err) {
			return []*State{}, nil
		}
		return nil, err
	}
	out := []*State{}
	for _, e := range entries {
		if !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		st, err := Load(strings.TrimSuffix(e.Name(), ".json"))
		if err == nil {
			out = append(out, st)
		}
	}
	return out, nil
}

// refresh marks a vm exited when its pid is gone or was recycled by another
// process. The marker is NANOVM_NAME= in the pid's environment, which the
// child sets before exec so it survives into the payload.
func (st *State) refresh() {
	if st.Status != "running" {
		return
	}
	env, err := os.ReadFile(fmt.Sprintf("/proc/%d/environ", st.Pid))
	if err != nil || !strings.Contains(string(env), "NANOVM_NAME="+st.Spec.Name+"\x00") {
		st.Status = "exited"
		st.save()
	}
}
