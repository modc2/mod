"""symbols - extract code structure, definitions, and references"""
import re
import ast
from pathlib import Path
from typing import Dict, Any, Optional, List


class Skill:
    description = "Extract code symbols: functions, classes, imports, variables. AST parsing for Python, regex for other languages."

    # regex patterns for non-Python languages
    PATTERNS = {
        ".js":   {"function": r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\(|function))',
                   "class": r'class\s+(\w+)', "import": r'(?:import|require)\s*\(?\s*[\'"]([^\'"]+)'},
        ".ts":   {"function": r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*(?::\s*\w+)?\s*=\s*(?:async\s+)?(?:\(|function))',
                   "class": r'class\s+(\w+)', "import": r'import\s+.*?from\s+[\'"]([^\'"]+)',
                   "interface": r'(?:interface|type)\s+(\w+)'},
        ".tsx":  {"function": r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*(?::\s*\w+)?\s*=\s*(?:async\s+)?(?:\(|function))',
                   "class": r'class\s+(\w+)', "import": r'import\s+.*?from\s+[\'"]([^\'"]+)',
                   "interface": r'(?:interface|type)\s+(\w+)'},
        ".jsx":  {"function": r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\(|function))',
                   "class": r'class\s+(\w+)', "import": r'import\s+.*?from\s+[\'"]([^\'"]+)'},
        ".rs":   {"function": r'(?:pub\s+)?(?:async\s+)?fn\s+(\w+)', "struct": r'(?:pub\s+)?struct\s+(\w+)',
                   "enum": r'(?:pub\s+)?enum\s+(\w+)', "trait": r'(?:pub\s+)?trait\s+(\w+)',
                   "impl": r'impl(?:<[^>]+>)?\s+(\w+)'},
        ".go":   {"function": r'func\s+(?:\([^)]+\)\s+)?(\w+)', "struct": r'type\s+(\w+)\s+struct',
                   "interface": r'type\s+(\w+)\s+interface', "import": r'"([^"]+)"'},
        ".sol":  {"function": r'function\s+(\w+)', "contract": r'contract\s+(\w+)',
                   "event": r'event\s+(\w+)', "modifier": r'modifier\s+(\w+)'},
        ".rb":   {"function": r'def\s+(\w+)', "class": r'class\s+(\w+)', "module": r'module\s+(\w+)'},
        ".java": {"function": r'(?:public|private|protected)\s+\w+\s+(\w+)\s*\(',
                   "class": r'class\s+(\w+)', "interface": r'interface\s+(\w+)'},
    }

    def forward(self, file_path: str, kind: str = None, **kwargs) -> Dict[str, Any]:
        """
        Extract symbols from a source file.

        Args:
            file_path: Path to source file
            kind: Filter by symbol kind (function, class, import, etc.). None = all.
        """
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            return {"success": False, "error": f"not a file: {path}"}

        ext = path.suffix.lower()
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return {"success": False, "error": str(e)}

        # Python: use AST for accurate parsing
        if ext == ".py":
            return self._parse_python(content, str(path), kind)

        # Other languages: regex
        if ext in self.PATTERNS:
            return self._parse_regex(content, str(path), ext, kind)

        return {"success": False, "error": f"unsupported file type: {ext}",
                "supported": [".py"] + list(self.PATTERNS.keys())}

    def _parse_python(self, content: str, path: str, kind: str = None) -> Dict[str, Any]:
        """Parse Python file using AST"""
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            return {"success": False, "error": f"syntax error: {e}"}

        symbols = []
        lines = content.splitlines()

        for node in ast.walk(tree):
            sym = None
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                args = [a.arg for a in node.args.args if a.arg != 'self']
                sym = {"kind": "function", "name": node.name, "line": node.lineno,
                       "args": args, "async": isinstance(node, ast.AsyncFunctionDef)}
                if node.decorator_list:
                    sym["decorators"] = [ast.dump(d) if not hasattr(d, 'id') else d.id for d in node.decorator_list]
            elif isinstance(node, ast.ClassDef):
                bases = []
                for b in node.bases:
                    if hasattr(b, 'id'):
                        bases.append(b.id)
                    elif hasattr(b, 'attr'):
                        bases.append(b.attr)
                methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                sym = {"kind": "class", "name": node.name, "line": node.lineno,
                       "bases": bases, "methods": methods}
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    symbols.append({"kind": "import", "name": alias.name,
                                    "alias": alias.asname, "line": node.lineno})
            elif isinstance(node, ast.ImportFrom):
                names = [a.name for a in node.names]
                sym = {"kind": "import", "name": node.module or "", "line": node.lineno, "names": names}
            elif isinstance(node, ast.Assign) and node.col_offset == 0:
                for target in node.targets:
                    if hasattr(target, 'id'):
                        sym = {"kind": "variable", "name": target.id, "line": node.lineno}

            if sym:
                if kind and sym["kind"] != kind:
                    continue
                symbols.append(sym)

        symbols.sort(key=lambda s: s.get("line", 0))
        return {"success": True, "path": path, "language": "python", "symbols": symbols, "total": len(symbols)}

    def _parse_regex(self, content: str, path: str, ext: str, kind: str = None) -> Dict[str, Any]:
        """Parse using regex patterns"""
        patterns = self.PATTERNS[ext]
        symbols = []
        lines = content.splitlines()

        for sym_kind, pattern in patterns.items():
            if kind and sym_kind != kind:
                continue
            for i, line in enumerate(lines):
                for m in re.finditer(pattern, line):
                    name = next((g for g in m.groups() if g), None)
                    if name:
                        symbols.append({"kind": sym_kind, "name": name, "line": i + 1, "text": line.strip()})

        symbols.sort(key=lambda s: s.get("line", 0))
        lang = {".js": "javascript", ".ts": "typescript", ".tsx": "tsx", ".jsx": "jsx",
                ".rs": "rust", ".go": "go", ".sol": "solidity", ".rb": "ruby", ".java": "java"}.get(ext, ext)
        return {"success": True, "path": path, "language": lang, "symbols": symbols, "total": len(symbols)}

    def test(self):
        r = self.forward(__file__)
        assert r["success"] and r["total"] > 0
        assert any(s["name"] == "Skill" for s in r["symbols"])
        return True
