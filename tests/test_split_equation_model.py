import base64
import json
from types import SimpleNamespace

import fitz
import httpx

from packages.paper_tools import PaperTools


def test_vision_model_receives_whole_split_fraction(tmp_path, monkeypatch):
    source = tmp_path / "split.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=400, height=300)
        page.insert_text((40, 50), "The following numbered equation defines a ratio.")
        page.insert_text((100, 110), "F =")
        page.insert_text((130, 100), "a")
        page.insert_text((130, 120), "b")
        page.draw_line((128, 106), (140, 106))
        page.insert_text((200, 110), "#(1)")
        page.insert_text((40, 160), "where a and b are measured quantities.")
        pdf.save(source)

    settings = SimpleNamespace(model_provider="openai_compatible", model_api_key="test-only-key",
        model_name="test-vision", model_base_url="https://example.invalid/v1",
        model_timeout=10, model_max_calls=3, model_max_tokens=1000)
    captured = []
    client_class = httpx.Client

    def respond(request):
        payload = json.loads(request.content)
        captured.append(payload)
        context = json.loads(payload["messages"][1]["content"][0]["text"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "latex": r"F = \frac{a}{b}", "explanation": None,
            "anchor_ids": [item["id"] for item in context["sources"]],
            "uncertain": True, "reasons": ["Review the original fraction"]})}}]})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs:
        client_class(transport=httpx.MockTransport(respond), **kwargs))
    tools = PaperTools(settings)
    parsed = tools.parse(source, "split-rendition")
    equations = tools.recognize(parsed, source, tmp_path / "evidence")

    assert len(equations) == len(captured) == 1
    assert equations[0]["latex"] == r"F = \frac{a}{b}"
    assert equations[0]["recognition"]["provider"] == "openai_compatible"
    assert equations[0]["recognition"]["status"] == "unknown"
    context = json.loads(captured[0]["messages"][1]["content"][0]["text"])
    quotes = " ".join(item["quote"] for item in context["sources"])
    assert all(value in quotes for value in ("F =", "a", "b", "#(1)"))
    assert "measured quantities" not in quotes
    picture = captured[0]["messages"][1]["content"][1]["image_url"]["url"]
    assert picture.startswith("data:image/png;base64,")
    image_data = base64.b64decode(picture.split(",", 1)[1])
    assert image_data == (tmp_path / "evidence" / "EQ-01.png").read_bytes()
    with fitz.open(stream=image_data, filetype="png") as image_pdf:
        # At 2x scale, the crop must cover numerator, denominator, and the marker.
        assert image_pdf[0].rect.height >= 40
