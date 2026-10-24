package vm

import (
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
)

// busybox applets worth a symlink in a minimal root. Everything else is
// reachable as `busybox <applet>` anyway.
var applets = []string{
	"sh", "ash", "ls", "cat", "echo", "ps", "top", "env", "hostname", "sleep",
	"mkdir", "rm", "cp", "mv", "ln", "touch", "grep", "sed", "awk", "wc",
	"head", "tail", "mount", "umount", "ip", "ping", "wget", "vi", "du", "df",
	"kill", "id", "uname", "date", "find", "xargs", "tar", "gzip", "chmod",
}

// BuildRootfs assembles a minimal busybox root filesystem in dir — enough to
// boot `nanovm run --rootfs dir sh`. It copies the host's busybox (static on
// Debian/Ubuntu/Alpine) rather than downloading anything: the framework stays
// offline and auditable. For a real distro root, `docker export`, debootstrap
// or an unpacked OCI layer into dir all work unchanged.
func BuildRootfs(dir string) (map[string]any, error) {
	bb, err := exec.LookPath("busybox")
	if err != nil {
		for _, p := range []string{"/bin/busybox", "/usr/bin/busybox", "/sbin/busybox"} {
			if _, e := os.Stat(p); e == nil {
				bb, err = p, nil
				break
			}
		}
	}
	if err != nil {
		return nil, fmt.Errorf("no busybox on the host — `apt install busybox-static`, or skip this helper and point --rootfs at a `docker export` / debootstrap tree")
	}
	for _, d := range []string{"bin", "etc", "proc", "dev", "tmp", "root", "usr/bin"} {
		if err := os.MkdirAll(filepath.Join(dir, d), 0o755); err != nil {
			return nil, err
		}
	}
	dst := filepath.Join(dir, "bin", "busybox")
	if err := copyFile(bb, dst, 0o755); err != nil {
		return nil, err
	}
	linked := 0
	for _, a := range applets {
		if err := os.Symlink("busybox", filepath.Join(dir, "bin", a)); err == nil || os.IsExist(err) {
			linked++
		}
	}
	os.WriteFile(filepath.Join(dir, "etc", "passwd"), []byte("root:x:0:0:root:/root:/bin/sh\n"), 0o644)
	os.WriteFile(filepath.Join(dir, "etc", "hostname"), []byte("nanovm\n"), 0o644)
	return map[string]any{
		"rootfs": dir, "busybox_from": bb, "applets": linked,
		"try": fmt.Sprintf("nanovm run --rootfs %s --mem 32M demo sh", dir),
	}, nil
}

func copyFile(src, dst string, mode os.FileMode) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, mode)
	if err != nil {
		return err
	}
	defer out.Close()
	_, err = io.Copy(out, in)
	return err
}
