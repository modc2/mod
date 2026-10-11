// jevbridge — one-shot CLI over jevgo (github.com/fgn/jevgo), the Go client
// for TypeSafe AI's System One API and the Jev model.
//
// Usage:  jevbridge <op>        request JSON on stdin, response JSON on stdout
// ops:    system_one | models | info
//
// Every network call the jev module makes goes through this binary, so the
// module's behaviour (options, retries, timeouts, error taxonomy) is exactly
// jevgo's. On failure it prints {"error":..,"code":..} and exits 1.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"time"

	jev "github.com/fgn/jevgo"
)

type wireQuestion struct {
	Kind         string         `json:"kind"` // noul | choice | score | raw
	Instructions any            `json:"instructions,omitempty"`
	Criteria     any            `json:"criteria,omitempty"`
	Raw          map[string]any `json:"raw,omitempty"`
}

type wireRequest struct {
	State      any                     `json:"state"`
	Questions  map[string]wireQuestion `json:"questions"`
	Model      string                  `json:"model,omitempty"`
	Extra      map[string]any          `json:"extra,omitempty"`
	APIKey     string                  `json:"api_key,omitempty"`
	BaseURL    string                  `json:"base_url,omitempty"`
	TimeoutS   float64                 `json:"timeout_s,omitempty"`
	MaxRetries *int                    `json:"max_retries,omitempty"`
}

func main() {
	if len(os.Args) != 2 {
		die("usage: jevbridge <system_one|models|info>", "usage")
	}
	op := os.Args[1]

	if op == "info" {
		info()
		return
	}

	var req wireRequest
	if raw, err := io.ReadAll(os.Stdin); err != nil {
		die("reading stdin: "+err.Error(), "usage")
	} else if len(raw) > 0 {
		if err := json.Unmarshal(raw, &req); err != nil {
			die("request is not valid JSON: "+err.Error(), "usage")
		}
	}

	client, err := jev.NewClient(options(req)...)
	if err != nil {
		fail(err)
	}
	ctx := context.Background()
	if req.TimeoutS > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, time.Duration(req.TimeoutS*float64(time.Second)))
		defer cancel()
	}

	switch op {
	case "system_one":
		systemOne(ctx, client, req)
	case "models":
		models(ctx, client)
	default:
		die("unknown op: "+op, "usage")
	}
}

func options(req wireRequest) []jev.Option {
	var opts []jev.Option
	if req.APIKey != "" {
		opts = append(opts, jev.WithAPIKey(req.APIKey))
	}
	if req.BaseURL != "" {
		opts = append(opts, jev.WithBaseURL(req.BaseURL))
	}
	if req.Model != "" {
		opts = append(opts, jev.WithDefaultModel(req.Model))
	}
	if req.TimeoutS > 0 {
		opts = append(opts, jev.WithTimeout(time.Duration(req.TimeoutS*float64(time.Second))))
	}
	if req.MaxRetries != nil {
		opts = append(opts, jev.WithMaxRetries(*req.MaxRetries))
	}
	return opts
}

func systemOne(ctx context.Context, client *jev.Client, req wireRequest) {
	questions := jev.Questions{}
	for name, q := range req.Questions {
		switch q.Kind {
		case "noul", "":
			questions[name] = jev.Noul{Instructions: q.Instructions}
		case "choice":
			questions[name] = jev.Choice{Instructions: q.Instructions, Criteria: q.Criteria}
		case "score":
			questions[name] = jev.Score{Instructions: q.Instructions, Criteria: q.Criteria}
		case "raw":
			questions[name] = jev.RawQuestion(q.Raw)
		default:
			die(fmt.Sprintf("question %q: unknown kind %q (noul|choice|score|raw)", name, q.Kind), "invalid_request")
		}
	}

	resp, err := client.SystemOne(ctx, jev.Request{
		State:     req.State,
		Questions: questions,
		Model:     req.Model,
		Extra:     req.Extra,
	})
	if err != nil {
		fail(err)
	}

	answers := map[string]any{}
	for name, a := range resp.Answers {
		switch v := a.(type) {
		case jev.NoulAnswer:
			answers[name] = map[string]any{"kind": "noul", "noul": v.Noul}
		case jev.ChoiceAnswer:
			answers[name] = map[string]any{
				"kind": "choice", "choice": v.Choice,
				"confidence": v.Confidence, "probabilities": v.Probabilities,
			}
		case jev.ScoreAnswer:
			answers[name] = map[string]any{
				"kind": "score", "score": v.Score, "confidence": v.Confidence,
				"legend": v.Legend, "probabilities": v.Probabilities,
			}
		default:
			// Sealed Answer implements json.Marshaler — pass unknown kinds through.
			b, err := json.Marshal(a)
			if err != nil {
				b = []byte(fmt.Sprintf("%q", fmt.Sprint(a)))
			}
			answers[name] = json.RawMessage(b)
		}
	}

	emit(map[string]any{
		"model":      resp.Model,
		"answers":    answers,
		"usage":      resp.Usage,
		"request_id": resp.RequestID,
		"attempts":   resp.Attempts,
	})
}

func models(ctx context.Context, client *jev.Client) {
	resp, err := client.ListModels(ctx)
	if err != nil {
		fail(err)
	}
	emit(map[string]any{"models": resp.Models, "request_id": resp.RequestID})
}

// info needs no client (and so no key): it reports what a client would use.
func info() {
	base := os.Getenv("TYPESAFE_BASE_URL")
	if base == "" {
		base = "https://api.typesafe.ai"
	}
	model := os.Getenv("TYPESAFE_DEFAULT_MODEL")
	if model == "" {
		model = "jev-latest"
	}
	emit(map[string]any{
		"client":        "github.com/fgn/jevgo",
		"base_url":      base,
		"default_model": model,
		"key_present":   os.Getenv("TYPESAFE_API_KEY") != "",
	})
}

// code maps every jevgo sentinel to a stable string the Python side returns verbatim.
func code(err error) string {
	for _, c := range []struct {
		sentinel error
		code     string
	}{
		{jev.ErrAuthentication, "authentication"},
		{jev.ErrPermissionDenied, "permission_denied"},
		{jev.ErrNotFound, "not_found"},
		{jev.ErrUnprocessableEntity, "unprocessable"},
		{jev.ErrRateLimit, "rate_limit"},
		{jev.ErrOverloaded, "overloaded"},
		{jev.ErrBadRequest, "bad_request"},
		{jev.ErrInternalServer, "internal"},
		{jev.ErrTimeout, "timeout"},
		{jev.ErrConnection, "connection"},
		{jev.ErrResponseTooLarge, "response_too_large"},
		{jev.ErrInvalidRequest, "invalid_request"},
		{jev.ErrInvalidConfig, "invalid_config"},
	} {
		if errors.Is(err, c.sentinel) {
			return c.code
		}
	}
	return "error"
}

func fail(err error)           { die(err.Error(), code(err)) }
func die(msg, code string)     { emitTo(os.Stdout, map[string]any{"error": msg, "code": code}); os.Exit(1) }
func emit(v any)               { emitTo(os.Stdout, v) }
func emitTo(w io.Writer, v any) {
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
}
