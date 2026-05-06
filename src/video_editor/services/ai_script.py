"""Generate a narration script from instructions, using the Anthropic Claude API."""
from __future__ import annotations

from typing import Optional

from ..core import config


SYSTEM_PROMPT = """You write narration scripts for video voice-overs.

Given the user's instructions, return ONLY the script — no preamble, no quotes,
no stage directions, no SSML, no headings, just the words to be spoken.

Keep it natural and easy to read aloud. Aim for the requested length;
default to about 60-90 words per requested minute of video. Use short
sentences and clear transitions.
"""


def generate_script(
    instructions: str,
    api_key: Optional[str] = None,
    target_seconds: Optional[float] = None,
    model: str = "claude-sonnet-4-6",
) -> str:
    """Generate a narration script.

    Raises RuntimeError on missing API key or API failure.
    """
    key = api_key or config.get("anthropic_api_key")
    if not key:
        raise RuntimeError(
            "No Anthropic API key set. Open Settings and paste your key."
        )

    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise RuntimeError(
            "anthropic package not installed. Run: pip install anthropic"
        ) from e

    client = Anthropic(api_key=key)

    user = instructions.strip()
    if target_seconds:
        approx_words = max(20, int(target_seconds * 2.5))  # ~150wpm
        user += f"\n\nTarget length: about {approx_words} words (~{target_seconds:.0f} seconds spoken)."

    msg = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    parts = []
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts).strip()
