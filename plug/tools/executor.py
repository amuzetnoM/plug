"""
Tool Executor
==============

Executes tool calls from the LLM and returns results as strings
suitable for feeding back into the conversation as tool-result messages.

All paths are resolved relative to the workspace root.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import shlex
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE = str(Path.home() / "workspace")
MEMORY_VENV = ".hektor-env/bin/activate"
MEMORY_SCRIPT = ".ava-memory/ava_memory_fast.py"


class ToolExecutor:
    """Executes tool calls and returns string results."""

    def __init__(self, workspace: str = DEFAULT_WORKSPACE):
        self.workspace = Path(workspace)
        self._http = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    async def close(self) -> None:
        await self._http.aclose()

    def _resolve_path(self, path: str) -> Path:
        """Resolve a path: absolute stays absolute, relative is joined to workspace."""
        p = Path(path)
        if p.is_absolute():
            return p
        return self.workspace / p

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """Dispatch a tool call by name. Returns result as string."""
        handlers = {
            "exec": self._exec,
            "read_file": self._read_file,
            "write_file": self._write_file,
            "edit_file": self._edit_file,
            "web_fetch": self._web_fetch,
            "memory_search": self._memory_search,
            "list_dir": self._list_dir,
        }

        handler = handlers.get(name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {name}"})

        try:
            result = await handler(**arguments)
            return result
        except Exception as e:
            logger.error("Tool %s failed: %s", name, e, exc_info=True)
            return json.dumps({"error": str(e)})

    # ── exec ─────────────────────────────────────────────────────────────

    async def _exec(
        self,
        command: str,
        timeout: int = 30,
        workdir: str | None = None,
    ) -> str:
        cwd = workdir or str(self.workspace)
        logger.info("exec: %s (cwd=%s, timeout=%ds)", command, cwd, timeout)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
                env={**os.environ, "TERM": "dumb", "NO_COLOR": "1", "PLUG_CALLER": "1"},
            )

            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return json.dumps({
                    "exit_code": -1,
                    "output": f"[Command timed out after {timeout}s]",
                    "timed_out": True,
                })

            output = stdout.decode("utf-8", errors="replace") if stdout else ""

            # Truncate if too long
            max_output = 50_000
            if len(output) > max_output:
                output = output[:max_output] + f"\n\n[Output truncated at {max_output} chars]"

            return json.dumps({
                "exit_code": proc.returncode,
                "output": output,
            })

        except Exception as e:
            return json.dumps({"error": f"exec failed: {e}"})

    # ── read_file ────────────────────────────────────────────────────────

    async def _read_file(
        self,
        path: str,
        offset: int = 1,
        limit: int | None = None,
    ) -> str:
        fpath = self._resolve_path(path)

        if not fpath.exists():
            return json.dumps({"error": f"File not found: {fpath}"})
        if not fpath.is_file():
            return json.dumps({"error": f"Not a file: {fpath}"})

        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines(keepends=True)
            total = len(lines)

            # Apply offset (1-based)
            start = max(0, offset - 1)
            if limit is not None:
                end = start + limit
            else:
                end = total

            selected = lines[start:end]
            content = "".join(selected)

            # Truncate if massive
            if len(content) > 100_000:
                content = content[:100_000] + "\n[Truncated]"

            return json.dumps({
                "path": str(fpath),
                "total_lines": total,
                "showing": f"{start + 1}-{min(end, total)}",
                "content": content,
            })
        except Exception as e:
            return json.dumps({"error": f"read_file failed: {e}"})

    # ── write_file ───────────────────────────────────────────────────────

    async def _write_file(self, path: str, content: str = "", text: str = "", data: str = "", **kwargs) -> str:
        # Accept content from multiple possible field names
        file_content = content or text or data
        if not file_content:
            return json.dumps({"error": "write_file requires 'content' parameter with the file contents. Call again with both 'path' and 'content'."})
        fpath = self._resolve_path(path)

        try:
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(file_content, encoding="utf-8")
            return json.dumps({
                "path": str(fpath),
                "bytes_written": len(file_content.encode("utf-8")),
                "success": True,
            })
        except Exception as e:
            return json.dumps({"error": f"write_file failed: {e}"})

    # ── edit_file ────────────────────────────────────────────────────────

    async def _edit_file(
        self, path: str, old_text: str, new_text: str
    ) -> str:
        fpath = self._resolve_path(path)

        if not fpath.exists():
            return json.dumps({"error": f"File not found: {fpath}"})

        try:
            content = fpath.read_text(encoding="utf-8")
            count = content.count(old_text)

            if count == 0:
                return json.dumps({
                    "error": "old_text not found in file",
                    "path": str(fpath),
                })
            if count > 1:
                return json.dumps({
                    "error": f"old_text found {count} times — must be unique. Add more context.",
                    "path": str(fpath),
                })

            new_content = content.replace(old_text, new_text, 1)
            fpath.write_text(new_content, encoding="utf-8")

            return json.dumps({
                "path": str(fpath),
                "replacements": 1,
                "success": True,
            })
        except Exception as e:
            return json.dumps({"error": f"edit_file failed: {e}"})

    # ── web_fetch ────────────────────────────────────────────────────────

    async def _web_fetch(self, url: str, max_chars: int = 50_000) -> str:
        try:
            resp = await self._http.get(url, headers={
                "User-Agent": "PLUG/0.1 (PLUG Bot)",
                "Accept": "text/html,text/plain,application/json,*/*",
            })
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            body = resp.text

            # If HTML, try to extract readable text
            if "html" in content_type.lower():
                body = self._html_to_text(body)

            if len(body) > max_chars:
                body = body[:max_chars] + f"\n\n[Truncated at {max_chars} chars]"

            return json.dumps({
                "url": str(resp.url),
                "status": resp.status_code,
                "content_type": content_type,
                "content": body,
            })
        except httpx.HTTPStatusError as e:
            return json.dumps({
                "error": f"HTTP {e.response.status_code}",
                "url": url,
            })
        except Exception as e:
            return json.dumps({"error": f"web_fetch failed: {e}"})

    @staticmethod
    def _html_to_text(html_content: str) -> str:
        """Basic HTML-to-text extraction."""
        # Remove script/style blocks
        text = re.sub(r"<script[^>]*>.*?</script>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        # Replace block elements with newlines
        text = re.sub(r"<(?:p|div|br|h[1-6]|li|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
        # Strip remaining tags
        text = re.sub(r"<[^>]+>", "", text)
        # Decode entities
        text = html.unescape(text)
        # Collapse whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()

    # ── memory_search ────────────────────────────────────────────────────

    async def _memory_search(
        self, query: str, mode: str = "hybrid", k: int = 5
    ) -> str:
        safe_query = shlex.quote(query)
        cmd = (
            f"cd {self.workspace} && "
            f"source {MEMORY_VENV} && "
            f"python3 {MEMORY_SCRIPT} search {safe_query} --mode {mode} -k {k}"
        )

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(self.workspace),
                env={**os.environ, "TERM": "dumb"},
                executable="/bin/bash",
            )

            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return json.dumps({"error": "Memory search timed out"})

            output = stdout.decode("utf-8", errors="replace") if stdout else ""

            if proc.returncode != 0:
                return json.dumps({
                    "error": f"Search exited with code {proc.returncode}",
                    "output": output[:5000],
                })

            return json.dumps({
                "query": query,
                "mode": mode,
                "results": output[:50_000],
            })

        except Exception as e:
            return json.dumps({"error": f"memory_search failed: {e}"})

    # ── list_dir ─────────────────────────────────────────────────────────

    async def _list_dir(self, path: str) -> str:
        dpath = self._resolve_path(path)

        if not dpath.exists():
            return json.dumps({"error": f"Directory not found: {dpath}"})
        if not dpath.is_dir():
            return json.dumps({"error": f"Not a directory: {dpath}"})

        try:
            entries = []
            for item in sorted(dpath.iterdir()):
                name = item.name
                if item.is_dir():
                    name += "/"
                entries.append(name)

            return json.dumps({
                "path": str(dpath),
                "count": len(entries),
                "entries": entries,
            })
        except Exception as e:
            return json.dumps({"error": f"list_dir failed: {e}"})
