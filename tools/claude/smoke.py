#!/usr/bin/env python3
"""One tiny live call to confirm the Anthropic key works (A35). Manual only: `make claude-smoke`. Never in CI.

Prints success or failure and the error class, nothing else: no key, no response text.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: E402

sys.path.insert(0, str(_env.ROOT / "api"))


def main() -> int:
    if not _env.load_key():
        print("claude-smoke: FAILED (ANTHROPIC_API_KEY is not set in the environment or .env)")
        return 1
    from app.ai import claude

    try:
        import anthropic

        client = anthropic.Anthropic(timeout=30, max_retries=1, default_headers=claude.workspace_headers())
        response = client.messages.create(
            model=claude.draft_model(), max_tokens=16, output_config={"effort": "low"},
            thinking={"type": "disabled"}, messages=[{"role": "user", "content": "Reply with the single word OK."}])
        ok = response.stop_reason in ("end_turn", "max_tokens") and any(b.type == "text" for b in response.content)
        print(f"claude-smoke: {'OK' if ok else 'FAILED (unexpected response)'} "
              f"(model {claude.draft_model()}, {response.usage.input_tokens} in / {response.usage.output_tokens} out tokens)")
        return 0 if ok else 1
    except Exception as exc:  # noqa: BLE001
        detail = ""
        body = getattr(getattr(exc, "response", None), "text", "")  # the server's error text; it never contains the key
        if body:
            detail = f": {body[:240]}"
        print(f"claude-smoke: FAILED ({type(exc).__name__}{detail})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
