"""code/rust eval - Rust coding tasks."""


class Eval:
    name = "code/rust"
    description = "Rust coding tasks: implement, fix, refactor, review, audit."
    language = "rust"
    owner = None  # inherit runtime owner key
    agents = [
        "default", "builder", "debugger", "refactorer",
        "reviewer", "safety", "architect",
        "agent", "claude",
    ]

    tasks = [
        {
            "prompt": (
                "Write a Rust function `fn word_count(text: &str) -> "
                "std::collections::HashMap<String, usize>` that returns a "
                "lowercase word frequency map. Idiomatic, no unwrap on user input."
            ),
            "checks": ["fn word_count", "HashMap", "to_lowercase"],
        },
        {
            "prompt": (
                "Implement a generic `RingBuffer<T>` with `push`, `pop`, "
                "and `len` methods using a fixed-capacity Vec. Handle wrap-around "
                "correctly and avoid panics on empty pop."
            ),
            "checks": ["struct RingBuffer", "impl<T>", "Option<T>"],
        },
        {
            "prompt": (
                "This Rust code does not compile because of a borrow-checker error. "
                "Identify the root cause and fix it.\n\n"
                "fn longest<'a>(x: &'a str, y: &str) -> &'a str {\n"
                "    if x.len() > y.len() { x } else { y }\n"
                "}\n"
            ),
            "checks": ["lifetime", "'a"],
        },
        {
            "prompt": (
                "Refactor this code to use the `?` operator and proper error "
                "propagation. Preserve behavior.\n\n"
                "fn read_num(path: &str) -> i32 {\n"
                "    let s = std::fs::read_to_string(path).unwrap();\n"
                "    s.trim().parse::<i32>().unwrap()\n"
                "}\n"
            ),
            "checks": ["Result<", "?", "Box<dyn"],
        },
        {
            "prompt": (
                "Review this Rust snippet for unsafety, panics, and correctness; "
                "report each finding with severity (CRITICAL/HIGH/MEDIUM/LOW):\n\n"
                "fn first(v: &Vec<i32>) -> i32 { unsafe { *v.get_unchecked(0) } }\n"
            ),
            "checks": ["unsafe", "bounds"],
        },
    ]
