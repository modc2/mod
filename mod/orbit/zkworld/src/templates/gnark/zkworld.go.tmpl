package main

// The zkworld runner (one per gnark project, keep it):
//
//	runner --abi          the circuit's inputs from Circuit's gnark tags
//	runner input.json     {"tag": "decimal", ...} -> solve the circuit;
//	                      exit 0 + {"outputs":{}} if every constraint holds
import (
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"strings"

	"github.com/consensys/gnark-crypto/ecc"
	"github.com/consensys/gnark/frontend"
	"github.com/consensys/gnark/frontend/cs/r1cs"
	"github.com/consensys/gnark/test"
)

type field struct {
	idx    int
	name   string
	public bool
}

func fields() []field {
	var out []field
	t := reflect.TypeOf(Circuit{})
	for i := 0; i < t.NumField(); i++ {
		tag := t.Field(i).Tag.Get("gnark")
		if tag == "-" {
			continue
		}
		parts := strings.Split(tag, ",")
		name := parts[0]
		if name == "" {
			name = t.Field(i).Name
		}
		pub := false
		for _, p := range parts[1:] {
			pub = pub || strings.TrimSpace(p) == "public"
		}
		out = append(out, field{i, name, pub})
	}
	return out
}

func die(format string, a ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", a...)
	os.Exit(1)
}

func main() {
	if len(os.Args) < 2 {
		die("usage: runner --abi | runner input.json")
	}
	if os.Args[1] == "--abi" {
		ins := []map[string]any{}
		for _, f := range fields() {
			ins = append(ins, map[string]any{"name": f.name, "public": f.public, "type": "field"})
		}
		abi := map[string]any{"circuit": "Circuit", "inputs": ins, "outputs": []any{}}
		if cs, err := frontend.Compile(ecc.BN254.ScalarField(), r1cs.NewBuilder, &Circuit{}); err == nil {
			abi["constraints"] = cs.GetNbConstraints()
		}
		b, _ := json.Marshal(abi)
		fmt.Println(string(b))
		return
	}
	raw, err := os.ReadFile(os.Args[1])
	if err != nil {
		die("read input: %v", err)
	}
	var in map[string]any
	if err := json.Unmarshal(raw, &in); err != nil {
		die("bad input json: %v", err)
	}
	var w Circuit
	v := reflect.ValueOf(&w).Elem()
	for _, f := range fields() {
		x, ok := in[f.name]
		if !ok {
			die("missing input %s", f.name)
		}
		v.Field(f.idx).Set(reflect.ValueOf(fmt.Sprint(x)))
	}
	if err := test.IsSolved(&Circuit{}, &w, ecc.BN254.ScalarField()); err != nil {
		die("constraint not satisfied: %v", strings.ReplaceAll(err.Error(), "\n", " "))
	}
	fmt.Println(`{"outputs":{}}`)
}
