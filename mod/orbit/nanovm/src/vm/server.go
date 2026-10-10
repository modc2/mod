package vm

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
)

const Version = "0.1.0"

// Serve runs the HTTP surface: the JSON API at the root and the console at
// /nanovm/. Failures answer as 4xx with a reason in the body, never 5xx —
// proxies in front of this fleet eat 5xx bodies, which turns a good error
// message into a blank page.
func Serve(port int) error {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		vms, _ := List()
		running := 0
		for _, v := range vms {
			if v.Status == "running" {
				running++
			}
		}
		ok(w, map[string]any{"ok": true, "port": port, "vms": len(vms), "running": running,
			"backend": "linux namespaces + pivot_root + cgroup v2", "data_dir": DataDir()})
	})
	mux.HandleFunc("/info", func(w http.ResponseWriter, r *http.Request) {
		ok(w, map[string]any{
			"name": "nanovm", "version": Version,
			"what": "micro-VMs out of nothing but the Linux kernel: namespaces, pivot_root, cgroup v2. Pure Go, zero dependencies, MIT.",
			"endpoints": map[string]string{
				"GET /health":            "liveness and a vm count",
				"GET /vms":               "every vm, liveness refreshed",
				"POST /vms":              "boot one: {name, cmd[], rootfs?, mem?, cpu?, pids?, net?, writable[]?, env[]?, dir?} — always detached",
				"GET /vms/{name}":        "one vm's state",
				"POST /vms/{name}/stop":  "SIGTERM, then SIGKILL",
				"POST /vms/{name}/exec":  "{cmd[]} run inside, output captured",
				"GET /vms/{name}/logs":   "last lines of a detached vm (?tail=100)",
				"DELETE /vms/{name}":     "stop and forget",
			},
		})
	})
	mux.HandleFunc("/vms", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			vms, err := List()
			if err != nil {
				bad(w, 400, err.Error())
				return
			}
			ok(w, map[string]any{"vms": vms, "count": len(vms)})
		case http.MethodPost:
			var s Spec
			if err := json.NewDecoder(r.Body).Decode(&s); err != nil {
				bad(w, 400, "body must be a Spec JSON: "+err.Error())
				return
			}
			s.Detach = true // an HTTP caller can't hold a foreground terminal
			st, err := Run(s)
			if err != nil {
				bad(w, 400, err.Error())
				return
			}
			ok(w, st)
		default:
			bad(w, 405, "GET or POST")
		}
	})
	mux.HandleFunc("/vms/", func(w http.ResponseWriter, r *http.Request) {
		parts := strings.Split(strings.Trim(strings.TrimPrefix(r.URL.Path, "/vms/"), "/"), "/")
		name := parts[0]
		action := ""
		if len(parts) > 1 {
			action = parts[1]
		}
		switch {
		case action == "" && r.Method == http.MethodGet:
			st, err := Load(name)
			if err != nil {
				bad(w, 404, err.Error())
				return
			}
			ok(w, st)
		case action == "" && r.Method == http.MethodDelete:
			if err := Remove(name); err != nil {
				bad(w, 404, err.Error())
				return
			}
			ok(w, map[string]any{"removed": name})
		case action == "stop" && r.Method == http.MethodPost:
			st, err := Stop(name)
			if err != nil {
				bad(w, 404, err.Error())
				return
			}
			ok(w, st)
		case action == "exec" && r.Method == http.MethodPost:
			var body struct {
				Cmd []string `json:"cmd"`
			}
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil || len(body.Cmd) == 0 {
				bad(w, 400, `body: {"cmd": ["sh","-c","..."]}`)
				return
			}
			out, code, err := Exec(name, body.Cmd, false)
			if err != nil {
				bad(w, 400, err.Error())
				return
			}
			ok(w, map[string]any{"output": out, "exit_code": code})
		case action == "logs" && r.Method == http.MethodGet:
			n, _ := strconv.Atoi(r.URL.Query().Get("tail"))
			if n == 0 {
				n = 100
			}
			out, err := Logs(name, n)
			if err != nil {
				bad(w, 404, err.Error())
				return
			}
			ok(w, map[string]any{"name": name, "logs": out})
		default:
			bad(w, 404, "unknown route under /vms/")
		}
	})
	console := func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Write([]byte(consoleHTML))
	}
	mux.HandleFunc("/nanovm/", console)
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			bad(w, 404, "no such route — GET /info lists them")
			return
		}
		console(w, r)
	})
	fmt.Printf("nanovm %s serving on :%d (console at /nanovm/)\n", Version, port)
	return http.ListenAndServe(fmt.Sprintf(":%d", port), mux)
}

func ok(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}

func bad(w http.ResponseWriter, code int, msg string) {
	if code >= 500 { // never 5xx: see Serve doc
		code = 400
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": msg})
}
