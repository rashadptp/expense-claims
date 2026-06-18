"""Groq vision extractor (free tier). Default provider for the demo."""
from __future__ import annotations

import base64
import json

from django.conf import settings

from .base import EXTRACTION_PROMPT, ExtractionResult, ReceiptExtractor


class GroqExtractor(ReceiptExtractor):
    name = "groq"

    def __init__(self):
        # Imported lazily so the package import never hard-depends on groq.
        from groq import Groq

        self.client = Groq(api_key=settings.GROQ_API_KEY)
        self.model = settings.GROQ_MODEL

    def extract(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> ExtractionResult:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{b64}"

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": EXTRACTION_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        )
        content = response.choices[0].message.content or "{}"
        data = json.loads(content)
        return ExtractionResult.from_payload(data, provider=self.name)
