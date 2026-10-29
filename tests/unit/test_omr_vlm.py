import httpx
import numpy as np
import pytest
import respx

from kff.read.omr import read_choices
from kff.read.vlm import VlmConfig, VlmReader, comb_strip, parse_reply


@pytest.mark.parametrize(
    ("fills", "multiple", "selected", "flagged"),
    [
        ({"a": 0.9, "b": 0.0}, False, ["a"], False),
        ({"a": 0.0, "b": 0.0}, False, [], False),
        ({"a": 0.3, "b": 0.01}, False, ["a"], False),  # a cross
        ({"a": 0.1, "b": 0.0}, False, [], True),  # faint: doubtful
        ({"a": 0.9, "b": 0.8}, False, ["a", "b"], True),  # two marked
        ({"a": 0.9, "b": 0.2}, False, ["a"], True),  # one filled, one brushed
        ({"a": 0.9, "b": 0.7, "c": 0.0}, True, ["a", "b"], False),
    ],
)
def test_read_choices(fills, multiple, selected, flagged):
    r = read_choices(fills, multiple)
    assert r.selected == selected
    assert (r.confidence < 0.75 or r.reason is not None) == flagged


@pytest.mark.parametrize(
    ("reply", "text", "conf"),
    [
        ('{"text": "AMINA KONE", "confidence": 0.9}', "AMINA KONE", 0.9),
        ('```json\n{"text": "أحمد", "confidence": 0.7}\n```', "أحمد", 0.7),
        ('Sure! {"text": "", "confidence": 1}', None, 1.0),
        ("AMINA", "AMINA", 0.3),
        ('{"text": "x", "confidence": 7}', "x", 1.0),
    ],
)
def test_parse_reply(reply, text, conf):
    r = parse_reply(reply)
    assert (r.text, r.confidence) == (text, conf)


def test_config_from_env():
    assert VlmConfig.from_env({}) is None
    c = VlmConfig.from_env({"KFF_VLM_URL": "http://llm:8080/v1/", "KFF_VLM_MODEL": "gemma"})
    assert (c.url, c.model, c.key) == ("http://llm:8080/v1", "gemma", None)


@respx.mock
def test_reader_sends_the_crop_and_reads_the_reply():
    route = respx.post("http://llm:8080/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": '{"text": "ROSE", "confidence": 0.8}'}}]}
        )
    )
    reader = VlmReader(VlmConfig("http://llm:8080/v1", key="k"))
    r = reader.read(np.full((40, 80), 255, np.uint8), "prompt")
    assert (r.text, r.confidence) == ("ROSE", 0.8)
    sent = route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer k"
    body = sent.read().decode()
    assert "data:image/png;base64," in body and '"temperature":0' in body.replace(" ", "")


def test_comb_strip_joins_cells():
    cells = [np.zeros((10, 8), np.uint8), np.zeros((12, 8), np.uint8)]
    strip = comb_strip(cells)
    assert strip.shape == (12 + 16, 8 + 6 + 8 + 16)
