"""
Tool Definitions — OpenAI function-calling format
===================================================

OpenAI function-calling tool schemas for PLUG.
"""

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "exec",
            "description": (
                "Execute a shell command on the host system. "
                "Returns stdout + stderr combined and the exit code. "
                "Working directory defaults to ~/workspace. "
                "Use for running scripts, git, system checks, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to run.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds. Default: 30.",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "Working directory. Default: ~/workspace.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the contents of a file. Supports offset/limit for partial reads."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or workspace-relative file path.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Starting line number (1-based). Default: 1.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read. Default: all.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create or overwrite a file with the given content. "
                "Parent directories are created automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or workspace-relative file path.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The full content to write to the file.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Surgical find-and-replace edit in a file. "
                "old_text must match exactly (including whitespace). "
                "Include surrounding context lines for a unique match."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or workspace-relative file path.",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "The exact text to find.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "The replacement text.",
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Fetch a URL and return readable content. "
                "HTML is converted to plain text / markdown."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Max response length in characters. Default: 50000.",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": (
                "Search workspace files using hybrid search "
                "(BM25 + vector). Finds file contents, past conversations, "
                "decisions, and documentation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["bm25", "vector", "hybrid"],
                        "description": "Search mode. Default: hybrid.",
                    },
                    "k": {
                        "type": "integer",
                        "description": "Number of results to return. Default: 5.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List contents of a directory. Returns filenames, one per line. Directories end with /.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or workspace-relative directory path.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "comb_stage",
            "description": (
                "Stage important information into your persistent memory (COMB). "
                "This survives restarts — use it to remember things across sessions. "
                "Stage key facts, lessons learned, task status, identity notes, anything you want to remember."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The information to remember. Be concise but complete.",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "comb_recall",
            "description": (
                "Recall your persistent memory from COMB. "
                "Returns everything you've staged across all previous sessions. "
                "Call this at the start of each session to remember who you are and what happened."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "discord_send",
            "description": (
                "Send a message to a Discord channel. Can include text, "
                "file attachments (images, documents, etc.), or both. "
                "Use this to post messages, share images, send files, "
                "or communicate in any Discord channel you have access to."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {
                        "type": "string",
                        "description": "The Discord channel ID to send to.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text message content. Optional if sending a file.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": (
                            "Path to a file to attach (image, document, etc.). "
                            "Absolute or workspace-relative. Optional."
                        ),
                    },
                    "reply_to": {
                        "type": "string",
                        "description": "Message ID to reply to. Optional.",
                    },
                },
                "required": ["channel_id"],
            },
        },
    },
]
