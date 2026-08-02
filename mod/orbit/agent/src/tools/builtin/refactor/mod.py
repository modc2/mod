"""refactor - rename symbols, extract functions, and restructure code"""
import re
import os
from pathlib import Path
from typing import Dict, Any, Optional, List


class Skill:
    description = "Refactoring operations: rename symbols across files, extract functions, find/replace with scope awareness."

    def forward(self, action: str, path: str = None, **kwargs) -> Dict[str, Any]:
        """
        Perform a refactoring operation.

        Args:
            action: One of: rename, extract, find_references, inline
            path: Project root or file path

        For action='rename':
            old_name (str): Current symbol name
            new_name (str): New symbol name
            file_pattern (str): Glob pattern for files to search (e.g. "*.py")

        For action='extract':
            file_path (str): File to extract from
            start_line (int): First line of code to extract
            end_line (int): Last line of code to extract
            function_name (str): Name for the new function

        For action='find_references':
            symbol (str): Symbol name to find
            file_pattern (str): Glob pattern for files
        """
        if action == "rename":
            return self._rename(path, **kwargs)
        elif action == "extract":
            return self._extract(path, **kwargs)
        elif action == "find_references":
            return self._find_refs(path, **kwargs)
        else:
            return {"success": False, "error": f"unknown action: {action}. use: rename, extract, find_references"}

    def _rename(self, path: str = None, old_name: str = None, new_name: str = None,
                file_pattern: str = None, **kwargs) -> Dict[str, Any]:
        """Rename a symbol across files"""
        if not old_name or not new_name:
            return {"success": False, "error": "old_name and new_name required"}

        cwd = Path(path or os.getcwd()).expanduser().resolve()
        pattern = f"**/{file_pattern}" if file_pattern else "**/*"
        skip = {'node_modules', '.git', '__pycache__', '.next', 'venv', '.venv', 'target', 'dist'}

        # use word boundary regex for safe renaming
        regex = re.compile(r'\b' + re.escape(old_name) + r'\b')

        changed_files = []
        total_replacements = 0

        for fp in cwd.glob(pattern):
            if not fp.is_file():
                continue
            if any(s in fp.parts for s in skip):
                continue
            # skip binary files
            try:
                with open(fp, 'rb') as f:
                    if b'\x00' in f.read(512):
                        continue
                content = fp.read_text(encoding="utf-8", errors="ignore")
            except (PermissionError, OSError):
                continue

            count = len(regex.findall(content))
            if count > 0:
                new_content = regex.sub(new_name, content)
                fp.write_text(new_content, encoding="utf-8")
                changed_files.append({"file": str(fp), "replacements": count})
                total_replacements += count

        return {
            "success": True,
            "old_name": old_name,
            "new_name": new_name,
            "files_changed": len(changed_files),
            "total_replacements": total_replacements,
            "changed": changed_files,
        }

    def _extract(self, path: str = None, file_path: str = None, start_line: int = None,
                 end_line: int = None, function_name: str = None, **kwargs) -> Dict[str, Any]:
        """Extract lines into a new function"""
        fp = Path(file_path or path).expanduser().resolve()
        if not fp.is_file():
            return {"success": False, "error": f"not a file: {fp}"}
        if not all([start_line, end_line, function_name]):
            return {"success": False, "error": "start_line, end_line, and function_name required"}

        try:
            lines = fp.read_text(encoding="utf-8").splitlines(keepends=True)
            if start_line < 1 or end_line > len(lines):
                return {"success": False, "error": f"line range {start_line}-{end_line} out of bounds (file has {len(lines)} lines)"}

            # extract the code block
            extracted = lines[start_line - 1:end_line]
            extracted_text = ''.join(extracted)

            # detect indentation of extracted code
            first_indent = len(extracted[0]) - len(extracted[0].lstrip()) if extracted else 0

            # build new function
            ext = fp.suffix.lower()
            if ext == ".py":
                # python function
                body = ''.join(f"    {l[first_indent:]}" if l.strip() else "\n" for l in extracted)
                func = f"\ndef {function_name}():\n{body}\n"
                call = f"{' ' * first_indent}{function_name}()\n"
            elif ext in (".js", ".ts", ".tsx", ".jsx"):
                body = ''.join(f"  {l[first_indent:]}" if l.strip() else "\n" for l in extracted)
                func = f"\nfunction {function_name}() {{\n{body}}}\n"
                call = f"{' ' * first_indent}{function_name}();\n"
            else:
                return {"success": False, "error": f"extract not supported for {ext}"}

            # replace extracted lines with function call
            new_lines = lines[:start_line - 1] + [call] + lines[end_line:]
            # append function definition at end
            new_content = ''.join(new_lines) + func

            fp.write_text(new_content, encoding="utf-8")
            return {
                "success": True,
                "function_name": function_name,
                "extracted_lines": f"{start_line}-{end_line}",
                "path": str(fp),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _find_refs(self, path: str = None, symbol: str = None,
                   file_pattern: str = None, **kwargs) -> Dict[str, Any]:
        """Find all references to a symbol"""
        if not symbol:
            return {"success": False, "error": "symbol name required"}

        cwd = Path(path or os.getcwd()).expanduser().resolve()
        pattern = f"**/{file_pattern}" if file_pattern else "**/*"
        skip = {'node_modules', '.git', '__pycache__', '.next', 'venv', '.venv', 'target', 'dist'}

        regex = re.compile(r'\b' + re.escape(symbol) + r'\b')
        refs = []

        for fp in cwd.glob(pattern):
            if not fp.is_file() or any(s in fp.parts for s in skip):
                continue
            try:
                with open(fp, 'rb') as f:
                    if b'\x00' in f.read(512):
                        continue
                lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
                for i, line in enumerate(lines):
                    if regex.search(line):
                        refs.append({"file": str(fp), "line": i + 1, "text": line.strip()})
                        if len(refs) >= 200:
                            return {"success": True, "symbol": symbol, "references": refs,
                                    "total": len(refs), "truncated": True}
            except (PermissionError, OSError):
                continue

        return {"success": True, "symbol": symbol, "references": refs, "total": len(refs)}

    def test(self):
        import tempfile
        # test find_references
        d = tempfile.mkdtemp()
        Path(d, "test.py").write_text("def foo():\n    pass\nfoo()\n")
        r = self.forward("find_references", path=d, symbol="foo", file_pattern="*.py")
        assert r["success"] and r["total"] >= 2
        # cleanup
        import shutil
        shutil.rmtree(d)
        return True
