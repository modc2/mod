"""debug - analyze errors, stack traces, and diagnose issues"""
import re
import traceback
from pathlib import Path
from typing import Dict, Any, Optional, List


class Skill:
    description = "Analyze stack traces, error messages, and logs. Extracts file locations, error types, and suggests fixes."

    def forward(self, error: str = None, log_file: str = None,
                last_n: int = 100, **kwargs) -> Dict[str, Any]:
        """
        Analyze an error message, stack trace, or log file.

        Args:
            error: Error text / stack trace to analyze
            log_file: Path to log file to analyze (reads last N lines)
            last_n: Number of lines to read from end of log file
        """
        if log_file:
            path = Path(log_file).expanduser().resolve()
            if not path.is_file():
                return {"success": False, "error": f"not a file: {path}"}
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
                error = "\n".join(lines[-last_n:])
            except Exception as e:
                return {"success": False, "error": str(e)}

        if not error:
            return {"success": False, "error": "provide error text or log_file path"}

        analysis = {
            "success": True,
            "error_type": None,
            "message": None,
            "locations": [],
            "frames": [],
            "suggestions": [],
        }

        # detect error type and message
        self._parse_error_type(error, analysis)

        # extract file locations from stack trace
        self._parse_locations(error, analysis)

        # parse stack frames
        self._parse_frames(error, analysis)

        # generate suggestions
        self._suggest(error, analysis)

        return analysis

    def _parse_error_type(self, error: str, analysis: dict):
        """Extract error type and message"""
        # Python errors
        m = re.search(r'^(\w+Error|\w+Exception|\w+Warning):\s*(.+)$', error, re.MULTILINE)
        if m:
            analysis["error_type"] = m.group(1)
            analysis["message"] = m.group(2).strip()
            return

        # JavaScript errors
        m = re.search(r'^(TypeError|ReferenceError|SyntaxError|RangeError|Error):\s*(.+)$', error, re.MULTILINE)
        if m:
            analysis["error_type"] = m.group(1)
            analysis["message"] = m.group(2).strip()
            return

        # Rust errors
        m = re.search(r'^error\[E\d+\]:\s*(.+)$', error, re.MULTILINE)
        if m:
            analysis["error_type"] = "RustCompileError"
            analysis["message"] = m.group(1).strip()
            return

        # Go errors
        m = re.search(r'^.*\.go:\d+:\d+:\s*(.+)$', error, re.MULTILINE)
        if m:
            analysis["error_type"] = "GoError"
            analysis["message"] = m.group(1).strip()
            return

        # Generic: first non-empty line
        for line in error.splitlines():
            if line.strip():
                analysis["message"] = line.strip()
                break

    def _parse_locations(self, error: str, analysis: dict):
        """Extract file:line locations"""
        patterns = [
            # Python: File "path.py", line N
            r'File "([^"]+)", line (\d+)',
            # JS/TS: at something (path.js:N:N)
            r'at .+\((.+):(\d+):\d+\)',
            # Rust: --> path.rs:N:N
            r'--> (.+):(\d+):\d+',
            # Go: path.go:N:N
            r'(\S+\.go):(\d+):\d+',
            # Generic: path:line
            r'(\S+\.\w{1,4}):(\d+)',
        ]
        seen = set()
        for pat in patterns:
            for m in re.finditer(pat, error):
                loc = {"file": m.group(1), "line": int(m.group(2))}
                key = f"{loc['file']}:{loc['line']}"
                if key not in seen:
                    seen.add(key)
                    analysis["locations"].append(loc)

    def _parse_frames(self, error: str, analysis: dict):
        """Parse stack frames"""
        # Python traceback
        py_frames = re.findall(
            r'File "([^"]+)", line (\d+), in (\w+)\n\s+(.+)',
            error
        )
        for f in py_frames:
            analysis["frames"].append({
                "file": f[0], "line": int(f[1]),
                "function": f[2], "code": f[3].strip()
            })

        # JS stack frames
        if not analysis["frames"]:
            js_frames = re.findall(
                r'at (\S+) \((.+):(\d+):\d+\)',
                error
            )
            for f in js_frames:
                analysis["frames"].append({
                    "function": f[0], "file": f[1], "line": int(f[2])
                })

    def _suggest(self, error: str, analysis: dict):
        """Generate fix suggestions based on error patterns"""
        msg = (analysis.get("message") or "").lower()
        etype = analysis.get("error_type") or ""

        suggestions = {
            "ModuleNotFoundError": "Install the missing module: pip install <module>",
            "ImportError": "Check import path and module name. Verify package is installed.",
            "NameError": "Variable or function not defined. Check spelling and scope.",
            "TypeError": "Wrong type passed to function. Check argument types.",
            "AttributeError": "Object doesn't have this attribute. Check the object type.",
            "KeyError": "Key not found in dict. Use .get() for safe access.",
            "IndexError": "List index out of range. Check list length before access.",
            "FileNotFoundError": "File doesn't exist. Check path and working directory.",
            "SyntaxError": "Check syntax near the indicated line. Look for missing brackets, quotes, colons.",
            "ValueError": "Invalid value. Check input data format and range.",
            "ConnectionError": "Network/connection issue. Check URL, port, and service status.",
            "PermissionError": "Insufficient permissions. Check file/directory permissions.",
        }

        if etype in suggestions:
            analysis["suggestions"].append(suggestions[etype])

        # pattern-based suggestions
        if "no module named" in msg:
            mod_name = re.search(r"no module named '?(\S+)'?", msg)
            if mod_name:
                analysis["suggestions"].append(f"pip install {mod_name.group(1)}")
        if "enoent" in msg.lower() or "no such file" in msg.lower():
            analysis["suggestions"].append("Check that the file path exists and is correct")
        if "permission denied" in msg.lower():
            analysis["suggestions"].append("Check file permissions: ls -la <file>")
        if "port" in msg and ("in use" in msg or "address already" in msg):
            analysis["suggestions"].append("Port already in use. Kill the process or use a different port.")
        if "timeout" in msg.lower():
            analysis["suggestions"].append("Request timed out. Check network, increase timeout, or retry.")
        if "cors" in msg.lower():
            analysis["suggestions"].append("CORS error. Add proper CORS headers to the server.")

    def test(self):
        trace = '''Traceback (most recent call last):
  File "app.py", line 42, in main
    result = process(data)
  File "app.py", line 15, in process
    return data["missing_key"]
KeyError: 'missing_key'
'''
        r = self.forward(error=trace)
        assert r["success"]
        assert r["error_type"] == "KeyError"
        assert len(r["locations"]) >= 2
        assert len(r["suggestions"]) > 0
        return True
