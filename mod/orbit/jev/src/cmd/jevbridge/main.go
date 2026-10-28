// jevbridge — one-shot CLI over jevgo (github.com/fgn/jevgo), the Go client
// for TypeSafe AI's System One API and the Jev model.
//
// Usage:  jevbridge <op>        request JSON on stdin, response JSON on stdout
// ops:    system_one | models | encode | info | version
//
// Every network call the jev module makes goes through this binary, so the
// module's behaviour (options, retries, timeouts, headers, logging, error
// taxonomy) is exactly jevgo's. The whole public surface of jevgo is reachable
// from the request JSON:
//
//	api_key, base_url, model          WithAPIKey / WithBaseURL / WithDefaultModel
//	timeout_s                          WithTimeout (+ a context deadline)
//	max_retries                        WithMaxRetries
//	retry {...}                        WithRetryPolicy (every RetryPolicy field)
//	headers {k: v}                     WithHeaders
//	log_level                          TYPESAFE_LOG_LEVEL for this call (debug|info|warn|error|off)
//	include_headers / include_raw      ResponseMetadata.Header / RawBody in the reply
//	trace                              the wire body jevgo sent (via a Tracer)
//
// On failure it prints {"error","code",...} and exits 1. API errors also carry
// status, request_id, retry_after_ms and the server message.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"time"

	jev "github.com/fgn/jevgo"
)

type wireQuestion struct {
	Kind         string         `json:"kind"` // noul | choice | score | raw
	Instructions any            `json:"instructions,omitempty"`
	Criteria     any            `json:"criteria,omitempty"`
	Raw          map[string]any `json:"raw,omitempty"`
}

type wireRetry struct {
	MaxRetries        *int     `json:"max_retries,omitempty"`
	InitialBackoffMs  *float64 `json:"initial_backoff_ms,omitempty"`
	MaxBackoffMs      *float64 `json:"max_backoff_ms,omitempty"`
	Jitter            *float64 `json:"jitter,omitempty"`
	Statuses          []int    `json:"statuses,omitempty"`
	RespectRetryAfter *bool    `json:"respect_retry_after,omitempty"`
	MaxRetryAfterMs   *float64 `json:"max_retry_after_ms,omitempty"`
	ConnectionErrors  *bool    `json:"connection_errors,omitempty"`
	Timeouts          *bool    `json:"timeouts,omitempty"`
	TotalTimeoutMs    *float64 `json:"total_timeout_ms,omitempty"`
}

type wireRequest struct {
	State          any                     `json:"state"`
	Questions      map[string]wireQuestion `json:"questions"`
	Model          string                  `json:"model,omitempty"`
	Extra          map[string]any          `json:"extra,omitempty"`
	APIKey         string                  `json:"api_key,omitempty"`
	BaseURL        string                  `json:"base_url,omitempty"`
	TimeoutS       float64                 `json:"timeout_s,omitempty"`
	MaxRetries     *int                    `json:"max_retries,omitempty"`
	Retry          *wireRetry              `json:"retry,omitempty"`
	Headers        map[string]string       `json:"headers,omitempty"`
	LogLevel       string                  `json:"log_level,omitempty"`
	IncludeHeaders bool                    `json:"include_headers,omitempty"`
	IncludeRaw     bool                    `json:"include_raw,omitempty"`
	Trace          bool                    `json:"trace,omitempty"`
}

func main() {
	if len(os.Args) != 2 {
		die("usage: jevbridge <system_one|models|encode|info|version>", "usage")
	}
	op := os.Args[1]

	switch op {
	case "info":
		info()
		return
	case "version":
		emit(map[string]any{"jevgo": jev.Version, "bridge": "0.2.0",
			"ops": []string{"system_one", "models", "encode", "info", "version"}})
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

	if req.LogLevel != "" {
		// jevgo reads TYPESAFE_LOG_LEVEL itself; records go to stderr so stdout stays JSON.
		os.Setenv(jev.EnvLogLevel, req.LogLevel)
		slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelDebug})))
	}

	opts, err := options(req)
	if err != nil {
		fail(err)
	}
	tracer := &capture{}
	if req.Trace || op == "encode" {
		opts = append(opts, jev.WithTracer(tracer))
	}
	if op == "encode" && req.APIKey == "" && os.Getenv(jev.EnvAPIKey) == "" {
		opts = append(opts, jev.WithAPIKey("offline")) // encode never sends
	}

	client, err := jev.NewClient(opts...)
	if err != nil {
		fail(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if req.TimeoutS > 0 {
		ctx, cancel = context.WithTimeout(ctx, time.Duration(req.TimeoutS*float64(time.Second)))
		defer cancel()
	}

	switch op {
	case "system_one":
		systemOne(ctx, client, req, tracer)
	case "encode":
		cancel() // validation + tracer run; the HTTP attempt never leaves the box
		encode(ctx, client, req, tracer)
	case "models":
		models(ctx, client, req)
	default:
		die("unknown op: "+op, "usage")
	}
}

func options(req wireRequest) ([]jev.Option, error) {
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
	if req.Retry != nil {
		opts = append(opts, jev.WithRetryPolicy(retryPolicy(*req.Retry)))
	}
	if len(req.Headers) > 0 {
		h := http.Header{}
		for k, v := range req.Headers {
			h.Set(k, v)
		}
		opts = append(opts, jev.WithHeaders(h))
	}
	return opts, nil
}

func retryPolicy(w wireRetry) jev.RetryPolicy {
	p := jev.DefaultRetryPolicy()
	ms := func(v *float64, dst *time.Duration) {
		if v != nil {
			*dst = time.Duration(*v * float64(time.Millisecond))
		}
	}
	if w.MaxRetries != nil {
		p.MaxRetries = *w.MaxRetries
	}
	ms(w.InitialBackoffMs, &p.InitialBackoff)
	ms(w.MaxBackoffMs, &p.MaxBackoff)
	ms(w.MaxRetryAfterMs, &p.MaxRetryAfter)
	ms(w.TotalTimeoutMs, &p.TotalTimeout)
	if w.Jitter != nil {
		p.Jitter = *w.Jitter
	}
	if w.Statuses != nil {
		p.Statuses = w.Statuses
	}
	if w.RespectRetryAfter != nil {
		p.RespectRetryAfter = *w.RespectRetryAfter
	}
	if w.ConnectionErrors != nil {
		p.ConnectionErrors = *w.ConnectionErrors
	}
	if w.Timeouts != nil {
		p.Timeouts = *w.Timeouts
	}
	return p
}

// capture is a Tracer that keeps the wire body jevgo sent (or would send).
type capture struct {
	model string
	body  json.RawMessage
	start bool
}

func (c *capture) TraceSystemOneStart(ctx context.Context, d jev.SystemOneStartData) context.Context {
	c.start, c.model, c.body = true, d.Model, append(json.RawMessage(nil), d.Body...)
	return ctx
}

func (c *capture) TraceSystemOneEnd(context.Context, jev.SystemOneEndData) {}

func questions(req wireRequest) jev.Questions {
	qs := jev.Questions{}
	for name, q := range req.Questions {
		switch q.Kind {
		case "noul", "":
			n := jev.Noul{Instructions: q.Instructions}
			if m, ok := q.Criteria.(map[string]any); ok {
				n.Criteria = &jev.NoulCriteria{True: m["true"], False: m["false"]}
			}
			qs[name] = n
		case "choice":
			qs[name] = jev.Choice{Instructions: q.Instructions, Criteria: q.Criteria}
		case "score":
			qs[name] = jev.Score{Instructions: q.Instructions, Criteria: q.Criteria}
		case "raw":
			raw := q.Raw
			if raw == nil {
				raw = map[string]any{}
				if q.Instructions != nil {
					raw["instructions"] = q.Instructions
				}
				if q.Criteria != nil {
					raw["criteria"] = q.Criteria
				}
			}
			qs[name] = jev.RawQuestion(raw)
		default:
			die(fmt.Sprintf("question %q: unknown kind %q (noul|choice|score|raw)", name, q.Kind), "invalid_request")
		}
	}
	return qs
}

func request(req wireRequest) jev.Request {
	return jev.Request{State: req.State, Questions: questions(req), Model: req.Model, Extra: req.Extra}
}

func systemOne(ctx context.Context, client *jev.Client, req wireRequest, tracer *capture) {
	resp, err := client.SystemOne(ctx, request(req))
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
				"kind": "score", "score": v.Score, "level": v.Level(), "confidence": v.Confidence,
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

	out := map[string]any{
		"model":       resp.Model,
		"answers":     answers,
		"usage":       resp.Usage,
		"request_id":  resp.RequestID,
		"attempts":    resp.Attempts,
		"status_code": resp.StatusCode,
	}
	metadata(out, resp.ResponseMetadata, req)
	if req.Trace && tracer.start {
		out["trace"] = map[string]any{"model": tracer.model, "body": tracer.body}
	}
	emit(out)
}

// encode validates a request exactly as SystemOne would and returns the wire
// body, without sending it: the context is already cancelled, so the first
// HTTP attempt fails locally after the tracer has seen the body.
func encode(ctx context.Context, client *jev.Client, req wireRequest, tracer *capture) {
	_, err := client.SystemOne(ctx, request(req))
	if tracer.start {
		emit(map[string]any{"valid": true, "model": tracer.model, "body": tracer.body})
		return
	}
	if err == nil {
		err = errors.New("jev: encode produced no wire body")
	}
	fail(err)
}

func models(ctx context.Context, client *jev.Client, req wireRequest) {
	resp, err := client.ListModels(ctx)
	if err != nil {
		fail(err)
	}
	out := map[string]any{"models": resp.Models, "request_id": resp.RequestID,
		"attempts": resp.Attempts, "status_code": resp.StatusCode}
	metadata(out, resp.ResponseMetadata, req)
	emit(out)
}

func metadata(out map[string]any, md jev.ResponseMetadata, req wireRequest) {
	if req.IncludeHeaders {
		h := map[string]string{}
		for k, v := range md.Header {
			h[k] = strings.Join(v, ", ")
		}
		out["headers"] = h
	}
	if req.IncludeRaw {
		out["raw"] = md.RawBody
	}
}

// info needs no client (and so no key): it reports what a client would use.
func info() {
	base := os.Getenv(jev.EnvBaseURL)
	if base == "" {
		base = jev.DefaultBaseURL
	}
	model := os.Getenv(jev.EnvDefaultModel)
	if model == "" {
		model = jev.DefaultModel
	}
	def := jev.DefaultRetryPolicy()
	emit(map[string]any{
		"client":        "github.com/fgn/jevgo",
		"version":       jev.Version,
		"base_url":      base,
		"default_model": model,
		"key_present":   os.Getenv(jev.EnvAPIKey) != "",
		"log_level":     os.Getenv(jev.EnvLogLevel),
		"timeout_s":     jev.DefaultTimeout.Seconds(),
		"retry": map[string]any{
			"max_retries": def.MaxRetries, "initial_backoff_ms": def.InitialBackoff.Milliseconds(),
			"max_backoff_ms": def.MaxBackoff.Milliseconds(), "jitter": def.Jitter,
			"statuses": "408, 429, 500-599", "respect_retry_after": def.RespectRetryAfter,
			"max_retry_after_ms": def.MaxRetryAfter.Milliseconds(),
		},
		"env": []string{jev.EnvAPIKey, jev.EnvBaseURL, jev.EnvDefaultModel, jev.EnvLogLevel},
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

func fail(err error) {
	out := map[string]any{"error": err.Error(), "code": code(err)}
	var api *jev.APIError
	if errors.As(err, &api) {
		out["status"] = api.StatusCode
		out["message"] = api.Message
		if api.RequestID != "" {
			out["request_id"] = api.RequestID
		}
		if d, ok := api.RetryAfter(); ok {
			out["retry_after_ms"] = d.Milliseconds()
		}
	}
	var val *jev.ResponseValidationError
	if errors.As(err, &val) {
		out["code"] = "invalid_response"
		if errors.Is(err, jev.ErrResponseTooLarge) {
			out["code"] = "response_too_large"
		}
	}
	emitTo(os.Stdout, out)
	os.Exit(1)
}

func die(msg, code string) { emitTo(os.Stdout, map[string]any{"error": msg, "code": code}); os.Exit(1) }
func emit(v any)           { emitTo(os.Stdout, v) }
func emitTo(w io.Writer, v any) {
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
}
