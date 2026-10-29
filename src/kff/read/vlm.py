"""Handwritten text through a vision-language model behind an OpenAI-compatible API.

Offline this is kobo-in-a-box's llama.cpp (Gemma 4 E4B with its vision projector,
`http://llm:8080/v1`); online any compatible service. Off unless `KFF_VLM_URL` is set:
crops of people's answers are only sent to the host configured here.
"""

import base64
import json
import os
import re
from dataclasses import dataclass

import cv2
import httpx
import numpy as np

from kff.errors import KffError


@dataclass(frozen=True)
class VlmConfig:
    url: str  # base, e.g. http://llm:8080/v1
    model: str = "local"
    key: str | None = None
    timeout: float = 120.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "VlmConfig | None":
        env = dict(os.environ if env is None else env)
        url = env.get("KFF_VLM_URL", "").strip().rstrip("/")
        if not url:
            return None
        return cls(
            url=url,
            model=env.get("KFF_VLM_MODEL", "local").strip() or "local",
            key=env.get("KFF_VLM_KEY") or None,
            timeout=float(env.get("KFF_VLM_TIMEOUT") or 120),
        )


@dataclass
class TextReading:
    text: str | None
    confidence: float


_JSON = re.compile(r"\{.*\}", re.DOTALL)


def prompt_for(label: str, languages: list[str], boxes: bool) -> str:
    how = (
        "The answer is written in capital letters, one per box; an empty box is a space."
        if boxes
        else "The answer is handwritten on the line(s)."
    )
    return (
        "This image is cut from a scanned paper form.\n"
        f"Question: {label}\n"
        f"The answer may be in: {', '.join(languages) or 'any language'}.\n"
        f"{how}\n"
        "Transcribe exactly what is written, in its own script; do not translate or "
        "correct it. Ignore printed lines and boxes. If nothing is written, use an empty "
        'string. Reply with JSON only: {"text": "...", "confidence": <0 to 1>}'
    )


def _png(image: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise KffError("Couldn't encode a crop for the vision model.")
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode()


class VlmReader:
    def __init__(self, config: VlmConfig, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        headers = {"Authorization": f"Bearer {config.key}"} if config.key else {}
        self._http = httpx.Client(timeout=config.timeout, headers=headers, transport=transport)

    def close(self) -> None:
        self._http.close()

    def read(self, image: np.ndarray, prompt: str) -> TextReading:
        body = {
            "model": self.config.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": _png(image)}},
                    ],
                }
            ],
        }
        try:
            resp = self._http.post(f"{self.config.url}/chat/completions", json=body)
        except httpx.HTTPError as err:
            raise KffError(f"The vision model at {self.config.url} didn't answer: {err}") from None
        if resp.status_code != 200:
            raise KffError(f"The vision model returned HTTP {resp.status_code}.")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise KffError("The vision model's reply wasn't a chat completion.") from None
        return parse_reply(content)


def parse_reply(content: str) -> TextReading:
    """`{"text": …, "confidence": …}`, maybe wrapped in prose or a code fence. A reply that
    isn't JSON is taken as the text, with low confidence."""
    m = _JSON.search(content or "")
    if m:
        try:
            data = json.loads(m.group(0))
            text = str(data.get("text") or "").strip()
            conf = float(data.get("confidence", 0.5))
            return TextReading(text or None, max(0.0, min(1.0, conf)))
        except (ValueError, TypeError, AttributeError):
            pass
    text = (content or "").strip().strip("`").strip()
    return TextReading(text or None, 0.3)


def comb_strip(crops: list[np.ndarray]) -> np.ndarray:
    """Letter-box crops side by side with a gap, one image for the model."""
    h = max(c.shape[0] for c in crops)
    gap = np.full((h, 6), 255, np.uint8)
    parts = []
    for c in crops:
        if c.shape[0] != h:
            c = cv2.copyMakeBorder(c, 0, h - c.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=255)
        parts += [c, gap]
    strip = np.hstack(parts[:-1])
    return cv2.copyMakeBorder(strip, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=255)
