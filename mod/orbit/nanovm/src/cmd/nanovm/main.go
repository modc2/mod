// nanovm — micro-VMs out of nothing but the Linux kernel.
//
// One static binary, pure Go, zero dependencies, MIT. A nanovm is a process
// behind its own PID/UTS/mount/IPC (and optionally net) namespaces, its own
// root via pivot_root, and a cgroup v2 slice for memory/cpu/pid caps.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"

	"nanovm/vm"
)

const usage = `nanovm — micro-VMs out of nothing but the Linux kernel (pure Go, zero deps, MIT)

  nanovm run [flags] NAME CMD...     boot a vm (foreground unless -d)
      --rootfs DIR   pivot into DIR (default: host root, read-only)
      --mem 64M      memory.max        --cpu 0.5     cores (cpu.max)
      --pids 128     pids.max          --net         share host network (default: none)
      --rw PATH      host path bound read-write inside (repeatable, host-root mode)
      --env K=V      extra environment (repeatable)
      --dir PATH     working directory inside
      -d             detach; stdout+stderr go to the vm's log
  nanovm ls [--json]                 every vm, liveness refreshed
  nanovm exec NAME CMD...            run a command inside a running vm (via nsenter)
  nanovm logs NAME [-n 100]          tail a detached vm's output
  nanovm stop NAME                   SIGTERM, then SIGKILL
  nanovm rm NAME                     stop and forget (state, log, cgroup)
  nanovm rootfs DIR                  assemble a minimal busybox rootfs in DIR
  nanovm serve [--port 51230]        HTTP API + web console
  nanovm version`

func main() {
	if vm.IsInit(os.Args) {
		vm.Child() // never returns
	}
	if len(os.Args) < 2 {
		fmt.Println(usage)
		return
	}
	var err error
	switch os.Args[1] {
	case "run":
		err = runCmd(os.Args[2:])
	case "ls", "ps":
		err = lsCmd(os.Args[2:])
	case "exec":
		if len(os.Args) < 4 {
			err = fmt.Errorf("usage: nanovm exec NAME CMD...")
			break
		}
		_, code, e := vm.Exec(os.Args[2], os.Args[3:], true)
		if e != nil {
			err = e
			break
		}
		os.Exit(code)
	case "logs":
		name, n := "", 100
		for i := 2; i < len(os.Args); i++ {
			if os.Args[i] == "-n" && i+1 < len(os.Args) {
				n, _ = strconv.Atoi(os.Args[i+1])
				i++
			} else {
				name = os.Args[i]
			}
		}
		out, e := vm.Logs(name, n)
		if e != nil {
			err = e
			break
		}
		fmt.Println(out)
	case "stop":
		err = need1(os.Args, func(name string) error {
			st, e := vm.Stop(name)
			if e == nil {
				fmt.Printf("%s stopped\n", st.Spec.Name)
			}
			return e
		})
	case "rm":
		err = need1(os.Args, func(name string) error {
			if e := vm.Remove(name); e != nil {
				return e
			}
			fmt.Printf("%s removed\n", name)
			return nil
		})
	case "rootfs":
		err = need1(os.Args, func(dir string) error {
			out, e := vm.BuildRootfs(dir)
			if e != nil {
				return e
			}
			return pj(out)
		})
	case "serve":
		port := 51230
		for i := 2; i < len(os.Args)-1; i++ {
			if os.Args[i] == "--port" {
				port, _ = strconv.Atoi(os.Args[i+1])
			}
		}
		err = vm.Serve(port)
	case "version":
		fmt.Println("nanovm", vm.Version)
	case "help", "--help", "-h":
		fmt.Println(usage)
	default:
		err = fmt.Errorf("unknown command %q — `nanovm help`", os.Args[1])
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "nanovm:", err)
		os.Exit(1)
	}
}

func runCmd(args []string) error {
	s := vm.Spec{}
	rest := []string{}
	takesValue := map[string]bool{"--rootfs": true, "--mem": true, "--cpu": true,
		"--pids": true, "--rw": true, "--env": true, "--dir": true}
	for i := 0; i < len(args); i++ {
		a := args[i]
		v := ""
		if takesValue[a] {
			if i+1 >= len(args) {
				return fmt.Errorf("%s needs a value", a)
			}
			i++
			v = args[i]
		}
		switch a {
		case "--rootfs":
			s.Rootfs = v
		case "--mem":
			s.Mem = v
		case "--cpu":
			s.CPU, _ = strconv.ParseFloat(v, 64)
		case "--pids":
			s.Pids, _ = strconv.Atoi(v)
		case "--net":
			s.Net = true
		case "--rw":
			s.Writable = append(s.Writable, v)
		case "--env":
			s.Env = append(s.Env, v)
		case "--dir":
			s.Dir = v
		case "-d", "--detach":
			s.Detach = true
		default:
			if strings.HasPrefix(a, "-") {
				return fmt.Errorf("unknown flag %q — `nanovm help`", a)
			}
			// first positional is NAME; everything after it is CMD, verbatim
			rest = append(rest, args[i:]...)
			i = len(args)
		}
	}
	if len(rest) < 2 {
		return fmt.Errorf("usage: nanovm run [flags] NAME CMD...")
	}
	s.Name, s.Cmd = rest[0], rest[1:]
	st, err := vm.Run(s)
	if err != nil {
		return err
	}
	if s.Detach {
		return pj(st)
	}
	os.Exit(st.ExitCode)
	return nil
}

func lsCmd(args []string) error {
	vms, err := vm.List()
	if err != nil {
		return err
	}
	if len(args) > 0 && args[0] == "--json" {
		return pj(map[string]any{"vms": vms, "count": len(vms)})
	}
	if len(vms) == 0 {
		fmt.Println("no vms — `nanovm run demo sleep 300` boots one")
		return nil
	}
	fmt.Printf("%-16s %-10s %-7s %-8s %s\n", "NAME", "STATUS", "PID", "MEM", "CMD")
	for _, v := range vms {
		pid, mem := "-", "-"
		if v.Status == "running" {
			pid = strconv.Itoa(v.Pid)
		}
		if v.Spec.Mem != "" {
			mem = v.Spec.Mem
		}
		fmt.Printf("%-16s %-10s %-7s %-8s %s\n", v.Spec.Name, v.Status, pid, mem, strings.Join(v.Spec.Cmd, " "))
	}
	return nil
}

func need1(args []string, f func(string) error) error {
	if len(args) < 3 {
		return fmt.Errorf("usage: nanovm %s NAME", args[1])
	}
	return f(args[2])
}

func pj(v any) error {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	fmt.Println(string(b))
	return nil
}
