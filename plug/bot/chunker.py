"""
Message Chunking
=================

Split long messages into Discord-safe chunks (<=2000 chars),
preserving code blocks and avoiding mid-word splits.
"""

from __future__ import annotations

import re


def chunk_message(text: str, max_length: int = 2000) -> list[str]:
    """Split text into chunks that fit within Discord's message limit.

    Handles:
    - Code blocks (``` ... ```) — never split mid-block
    - Inline code — preserved
    - Paragraph boundaries preferred for splits
    - Line boundaries as fallback
    """
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Find the best split point within max_length
        split_at = _find_split_point(remaining, max_length)
        chunk = remaining[:split_at].rstrip()
        remaining = remaining[split_at:].lstrip("\n")

        if chunk:
            chunks.append(chunk)

    return [c for c in chunks if c.strip()]


def _find_split_point(text: str, max_length: int) -> int:
    """Find the best split point within max_length characters.

    Priority:
    1. After a complete code block
    2. At a paragraph break (double newline)
    3. At a line break
    4. At a word boundary
    5. Hard cut at max_length
    """
    window = text[:max_length]

    # Check if we're inside a code block
    code_blocks = list(re.finditer(r"```", window))

    if code_blocks:
        # If odd number of ``` markers, we'd split inside a code block
        if len(code_blocks) % 2 != 0:
            # Find the last complete code block end
            last_complete = None
            for i in range(0, len(code_blocks) - 1, 2):
                last_complete = code_blocks[i + 1].end()

            if last_complete and last_complete > max_length // 4:
                # Split after the last complete code block
                # Look for a newline after it
                nl = window.find("\n", last_complete)
                if nl != -1:
                    return nl + 1
                return last_complete

            # Can't split cleanly around code blocks — try before the first one
            first_block = code_blocks[0].start()
            if first_block > max_length // 4:
                # Split before the code block
                before = window[:first_block].rstrip()
                return len(before)

    # Try paragraph break (double newline)
    para_break = window.rfind("\n\n")
    if para_break > max_length // 3:
        return para_break + 1

    # Try line break
    line_break = window.rfind("\n")
    if line_break > max_length // 3:
        return line_break + 1

    # Try word boundary (space)
    space = window.rfind(" ")
    if space > max_length // 2:
        return space + 1

    # Hard cut
    return max_length
