"""code/typescript eval - TypeScript coding tasks."""


class Eval:
    name = "code/typescript"
    description = "TypeScript coding tasks: implement, fix, refactor, review, audit."
    language = "typescript"
    owner = None  # inherit runtime owner key
    agents = [
        "default", "builder", "debugger", "refactorer",
        "reviewer", "safety", "architect",
        "agent", "claude",
    ]

    tasks = [
        {
            "prompt": (
                "Write a TypeScript function `groupBy<T, K extends string>"
                "(items: T[], keyOf: (item: T) => K): Record<K, T[]>` that groups "
                "items by the key returned from `keyOf`. Strict types only."
            ),
            "checks": ["function groupBy", "Record<", "T[]"],
        },
        {
            "prompt": (
                "Implement a typed `EventBus` class with `on<T>(event: string, "
                "handler: (payload: T) => void)`, `off`, and `emit<T>` methods. "
                "Use generics to keep payload types correct."
            ),
            "checks": ["class EventBus", "<T>", "emit"],
        },
        {
            "prompt": (
                "The async function below leaks unhandled promise rejections. "
                "Identify the root cause and fix it.\n\n"
                "async function fetchAll(urls: string[]) {\n"
                "  return urls.map(u => fetch(u).then(r => r.json()));\n"
                "}\n"
            ),
            "checks": ["Promise.all", "await"],
        },
        {
            "prompt": (
                "Refactor this code to use a discriminated union and exhaustive "
                "switch. Preserve behavior.\n\n"
                "type Shape = { kind: string; r?: number; w?: number; h?: number };\n"
                "function area(s: Shape) {\n"
                "  if (s.kind === 'circle') return Math.PI * s.r! * s.r!;\n"
                "  if (s.kind === 'rect') return s.w! * s.h!;\n"
                "  return 0;\n"
                "}\n"
            ),
            "checks": ["type Shape", "switch", "never"],
        },
        {
            "prompt": (
                "Review this Express handler for security and correctness; "
                "report each finding with severity (CRITICAL/HIGH/MEDIUM/LOW):\n\n"
                "app.get('/file', (req, res) => {\n"
                "  res.sendFile(__dirname + '/' + req.query.name);\n"
                "});\n"
            ),
            "checks": ["path traversal", "validate"],
        },
    ]
