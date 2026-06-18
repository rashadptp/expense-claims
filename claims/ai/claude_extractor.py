"""
Claude vision extractor — the production swap-in.

Activate by setting in .env:
    AI_PROVIDER=claude
    ANTHROPIC_API_KEY=sk-ant-...
and `pip install anthropic`. Same contract as GroqExtractor, so nothing else
in the app changes.
"""
from __future__ import annotations

import base64
import json

from django.conf import settings

from .base import EXTRACTION_PROMPT, ExtractionResult, ReceiptExtractor


class ClaudeExtractor(ReceiptExtractor):
    name = "claude"

    def __init__(self):
        from anthropic import Anthropic

        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = settings.CLAUDE_MODEL

    def extract(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> ExtractionResult:
        b64 = base64.b64encode(image_bytes).decode("utf-8")

        message = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": EXTRACTION_PROMPT
                            + "\n\nReturn the JSON object only.",
                        },
                    ],
                }
            ],
        )
        text = "".join(
            block.text for block in message.content if block.type == "text"
        ).strip()
        # Be tolerant of stray markdown fencing.
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1]
        data = json.loads(text)
        return ExtractionResult.from_payload(data, provider=self.name)
