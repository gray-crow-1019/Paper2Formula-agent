from pathlib import Path
from types import SimpleNamespace
import json
import zipfile

import fitz
import httpx
import pytest

from packages.paper_tools import PaperToolError, PaperTools
from packages.paper_tools.common import DOCX_MIME, PDF_MIME, sha256
from packages.paper_tools.validate import validate_syntax, validate_units


@pytest.fixture
def settings():
    return SimpleNamespace(max_upload_mb=10, max_pages=20, min_text_chars=10,
                           libreoffice_path="missing-soffice", conversion_timeout=30,
                           model_provider="heuristic", model_api_key="", model_base_url="",
                           model_name="", model_timeout=10, model_max_calls=3, model_max_tokens=1000)


def paper(path: Path, rotation=0, separate_number=False):
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((60, 60), "1 Introduction")
        page.insert_text((100, 110), "F = m * a" + ("" if separate_number else " (1)"))
        if separate_number:
            page.insert_text((450, 110), "(1)")
        page.insert_text((60, 150), "where F is force (N), m is mass (kg), and a is acceleration (m/s^2).")
        page.insert_text((60, 175), "The measured values are m = 2 kg and a = 3 m/s^2.")
        page.set_rotation(rotation)
        document.save(path)


def process(tool, path, output):
    normalized = tool.normalize(path, PDF_MIME, output / "normalized")
    parsed = tool.parse(normalized["pdf_path"], "rendition-test")
    equations = tool.recognize(parsed, Path(normalized["pdf_path"]), output / "evidence")
    tool.resolve(equations, parsed)
    return normalized, parsed, {"schema_version": "1.0", "document_id": "document-test",
        "rendition_id": "rendition-test", "result_version": 1, "status": "draft",
        "anchors": parsed["anchors"], "equations": equations, "review_items": [],
        "relations": tool.relate(equations, parsed), "manifest": {}}


@pytest.mark.parametrize("rotation,separate_number", [(0, False), (90, True)])
def test_real_text_pdf_roundtrip(tmp_path, settings, rotation, separate_number):
    original = tmp_path / "source.pdf"
    paper(original, rotation, separate_number)
    before = sha256(original)
    tools = PaperTools(settings)
    normalized, parsed, result = process(tools, original, tmp_path / "outputs")
    assert normalized["page_count"] == 1
    assert parsed["pages"][0]["page_width"] == 600
    assert parsed["pages"][0]["rotation"] == rotation
    assert len(result["equations"]) == 1
    equation = result["equations"][0]
    assert equation["original_number"] == "1"
    assert {symbol["name"] for symbol in equation["symbols"]} == {"F", "m", "a"}
    m = next(symbol for symbol in equation["symbols"] if symbol["name"] == "m")
    assert m["meaning"] == "mass (kg)"
    assert m["values"][0]["raw_value"] == "2"
    assert m["values"][0]["raw_unit"] == "kg"
    assert Path(equation["evidence_image"]).read_bytes().startswith(b"\x89PNG")
    tools.validate(result)
    assert result["status"] == "waiting_review"
    assert equation["validation"]["units"]["status"] == "pass"
    review = result["review_items"][0]
    assert review["kind"] == "recognition"
    review["status"] = "resolved"
    review["decision"] = {"action": "keep_unknown"}
    tools.validate(result)
    assert result["status"] == "validated"
    assert result["review_items"][0]["id"] == review["id"]
    exported = tools.export(result, Path(normalized["pdf_path"]), tmp_path / "artifacts")
    assert set(exported["artifacts"]) == {"markdown", "json", "annotated_pdf"}
    document = json.loads(Path(exported["artifacts"]["json"]["path"]).read_text(encoding="utf-8"))
    assert document["equations"][0]["evidence_image"] == "evidence/EQ-01.png"
    with fitz.open(exported["artifacts"]["annotated_pdf"]["path"]) as annotated:
        assert sum(1 for _ in annotated[0].annots()) >= 4
    assert sha256(original) == before


def test_missing_definitions_are_unknown_and_decisions_survive(tmp_path, settings):
    source = tmp_path / "unknown.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((80, 80), "x = y + 1 (1)")
        page.insert_text((80, 110), "No definitions are provided in this example.")
        document.save(source)
    tools = PaperTools(settings)
    _, _, result = process(tools, source, tmp_path / "outputs")
    tools.validate(result)
    assert result["equations"][0]["validation"]["units"]["status"] == "unknown"
    assert all(symbol["meaning"] is None for symbol in result["equations"][0]["symbols"])
    ids = [review["id"] for review in result["review_items"]]
    for review in result["review_items"]:
        review["status"] = "resolved"
        review["decision"] = {"action": "keep_unknown"}
    tools.validate(result)
    assert result["status"] == "validated"
    assert [review["id"] for review in result["review_items"]] == ids
    assert result["equations"][0]["validation"]["definitions"]["status"] == "unknown"


def test_scan_encrypted_and_page_limit(tmp_path, settings):
    tools = PaperTools(settings)
    scan = tmp_path / "scan.pdf"
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
    pixmap.clear_with(200)
    with fitz.open() as document:
        page = document.new_page()
        page.insert_image(page.rect, pixmap=pixmap)
        document.save(scan)
    with pytest.raises(PaperToolError, match="扫描") as exc:
        tools.inspect(scan, PDF_MIME)
    assert exc.value.code == "UNSUPPORTED_SCAN"
    encrypted = tmp_path / "encrypted.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((70, 70), "F = m * a (1)")
        document.save(encrypted, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
    with pytest.raises(PaperToolError) as exc:
        tools.inspect(encrypted, PDF_MIME)
    assert exc.value.code == "ENCRYPTED_DOCUMENT"
    good = tmp_path / "good.pdf"
    paper(good)
    settings.max_pages = 0
    with pytest.raises(PaperToolError) as exc:
        tools.inspect(good, PDF_MIME)
    assert exc.value.code == "PAGE_LIMIT"


def docx(path, external=False, macro=False):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="x" Target="' + ('https://example.com" TargetMode="External' if external else 'word/document.xml') + '"/></Relationships>')
        if macro:
            archive.writestr("word/vbaProject.bin", b"macro")


@pytest.mark.parametrize("external,macro", [(True, False), (False, True)])
def test_unsafe_docx_is_rejected(tmp_path, settings, external, macro):
    source = tmp_path / "source.docx"
    docx(source, external, macro)
    with pytest.raises(PaperToolError) as exc:
        PaperTools(settings).inspect(source, DOCX_MIME)
    assert exc.value.code == "INVALID_FILE"


def test_invalid_evidence_cannot_be_confirmed(tmp_path, settings):
    source = tmp_path / "source.pdf"
    paper(source)
    tools = PaperTools(settings)
    _, _, result = process(tools, source, tmp_path / "outputs")
    result["equations"][0]["symbols"][0]["evidence_anchor_ids"] = ["FAKE-ANCHOR"]
    with pytest.raises(PaperToolError) as exc:
        tools.validate(result)
    assert exc.value.code == "SOURCE_MISMATCH"


def test_bounded_math_checker_does_not_execute_python(tmp_path):
    sentinel = tmp_path / "executed"
    unsafe = f"__import__('pathlib').Path({str(sentinel)!r}).touch()"
    assert validate_syntax(unsafe)["status"] != "pass"
    assert not sentinel.exists()
    equation = {"latex": "F = m * a", "symbols": [
        {"name": "F", "values": [{"raw_unit": "kg"}]},
        {"name": "m", "values": [{"raw_unit": "kg"}]},
        {"name": "a", "values": [{"raw_unit": "m/s^2"}]},
    ]}
    assert validate_units(equation)["status"] == "fail"
    equation["symbols"][0]["values"][0]["raw_unit"] = None
    assert validate_units(equation)["status"] == "unknown"
    oversized = {"latex": "x = " + "9" * 1000,
                 "symbols": [{"name": "x", "values": [{"raw_unit": "1"}]}]}
    assert validate_syntax(oversized["latex"])["status"] == "unknown"
    assert validate_units(oversized)["status"] == "unknown"
    assert validate_units({"latex": None})["status"] == "unknown"


def test_edited_formula_adds_unknown_symbols_and_respects_exclusion(tmp_path, settings):
    source = tmp_path / "source.pdf"
    paper(source)
    tools = PaperTools(settings)
    _, _, result = process(tools, source, tmp_path / "outputs")
    tools.validate(result)
    equation = result["equations"][0]
    for review in result["review_items"]:
        review["status"] = "resolved"
        review["decision"] = {"action": "keep_unknown"}
    equation["latex"] = "F = m * b"
    equation["edited_latex"] = True
    equation["recognition"]["reviewed"] = True
    tools.validate(result)
    assert next(symbol for symbol in equation["symbols"] if symbol["name"] == "a")["status"] == "excluded"
    added = next(symbol for symbol in equation["symbols"] if symbol["name"] == "b")
    assert added["meaning"] is None
    assert equation["validation"]["definitions"]["status"] == "unknown"
    assert any(item["symbol_id"] == added["id"] and item["status"] == "pending" for item in result["review_items"])
    equation["excluded"] = True
    tools.validate(result)
    assert equation["status"] == "excluded"
    assert not any(item["equation_id"] == equation["id"] and item["status"] == "pending" for item in result["review_items"])


def test_changed_unit_reopens_only_affected_finding(tmp_path, settings):
    source = tmp_path / "source.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((80, 80), "x = y + 1 (1)")
        page.insert_text((80, 110), "No definitions are provided in this example.")
        document.save(source)
    tools = PaperTools(settings)
    _, _, result = process(tools, source, tmp_path / "outputs")
    tools.validate(result)
    for review in result["review_items"]:
        review["status"] = "resolved"
        review["decision"] = {"action": "keep_unknown"}
    equation = result["equations"][0]
    x = next(symbol for symbol in equation["symbols"] if symbol["name"] == "x")
    prior = next(item for item in result["review_items"] if item["symbol_id"] == x["id"] and item["kind"] == "units")
    x["values"][0]["raw_unit"] = "banana"
    x["values"][0]["provenance"] = "user_supplied"
    tools.validate(result)
    pending = [item for item in result["review_items"] if item["status"] == "pending"]
    assert len(pending) == 1
    assert pending[0]["symbol_id"] == x["id"]
    assert pending[0]["id"] != prior["id"]
    pending_id = pending[0]["id"]
    tools.validate(result)
    assert [item["id"] for item in result["review_items"] if item["status"] == "pending"] == [pending_id]
    assert any(item["id"] == prior["id"] and item["status"] == "resolved" for item in result["review_items"])


def test_model_rejects_unauthorized_evidence_without_network(tmp_path, settings, monkeypatch):
    from packages.providers.model import CompatibleModel
    settings.model_name = "test-vision"
    settings.model_api_key = "test-only-key"
    settings.model_base_url = "https://example.invalid/v1"
    image = tmp_path / "evidence.png"
    image.write_bytes(b"image-payload")
    client_class = httpx.Client
    captured = []
    def response(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "latex": "F = m * a", "explanation": None, "anchor_ids": ["FAKE-ANCHOR"],
            "uncertain": False, "reasons": []})}}]})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_class(transport=httpx.MockTransport(response), **kwargs))
    with pytest.raises(PaperToolError) as exc:
        CompatibleModel(settings).recognize({"text": "F=m*a"}, [{"id": "ANC-01", "quote": "F=m*a"}], image)
    assert exc.value.code == "SOURCE_MISMATCH"
    assert captured[0]["max_tokens"] == settings.model_max_tokens
    assert captured[0]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize("hook,expected_requests", [("claim_model_call", 0), ("check_processing_budget", 1)])
def test_model_persistent_budget_hooks_stop_execution(tmp_path, settings, monkeypatch, hook, expected_requests):
    from packages.providers.model import CompatibleModel
    settings.model_name = "test-vision"
    settings.model_api_key = "test-only-key"
    settings.model_base_url = "https://example.invalid/v1"
    image = tmp_path / "evidence.png"
    image.write_bytes(b"image-payload")
    captured = []
    client_class = httpx.Client
    def response(request):
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "latex": "F = m * a", "explanation": None, "anchor_ids": ["ANC-01"],
            "uncertain": True, "reasons": ["needs review"]})}}]})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_class(transport=httpx.MockTransport(response), **kwargs))
    def exhausted():
        raise PaperToolError("TASK_BUDGET", "Persistent task budget exhausted")
    setattr(settings, hook, exhausted)
    with pytest.raises(PaperToolError) as exc:
        CompatibleModel(settings).recognize({"text": "F=m*a"}, [{"id": "ANC-01", "quote": "F=m*a"}], image)
    assert exc.value.code == "TASK_BUDGET"
    assert len(captured) == expected_requests


@pytest.mark.parametrize("rotation", [0, 90])
def test_word_style_numbered_fragment_regions_preserve_exact_sources(tmp_path, settings, rotation):
    source = tmp_path / "word-style.pdf"
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((60, 70), "The formula is introduced below.")
        for position, text in [((180, 111), "Q="), ((210, 103), "u"), ((210, 120), "v"),
                               ((223, 111), "=Cexp("), ((268, 103), "-H"), ((268, 120), "rZ"),
                               ((292, 111), ")#(7)")]:
            page.insert_text(position, text, fontsize=12)
        page.insert_text((60, 135), "The parameters are defined in the following paragraph.")
        page.insert_text((180, 170), "ln(Q)=ln(C)-H", fontsize=12)
        page.insert_text((255, 184), "rZ", fontsize=12)
        page.insert_text((290, 170), "#(8)", fontsize=12)
        page.insert_text((60, 220), "The comparison x = y is discussed (9)")
        page.insert_text((300, 265), "#(10)")
        page.set_rotation(rotation)
        document.save(source)
    parsed = PaperTools(settings).parse(source, "word-rendition")
    assert [candidate["original_number"] for candidate in parsed["candidates"]] == ["7", "8"]
    anchors = {source["id"]: source for source in parsed["anchors"]}
    with fitz.open(source) as document:
        actual_lines = {"".join(span["text"] for span in line["spans"]).strip()
                        for block in document[0].get_text("dict")["blocks"]
                        for line in block.get("lines", [])}
    for candidate in parsed["candidates"]:
        assert candidate["layout_grouped"]
        assert len(candidate["anchor_ids"]) >= 3
        assert all(anchors[identifier]["quote"] in actual_lines for identifier in candidate["anchor_ids"])
        assert not any("paragraph" in anchors[identifier]["quote"] or "comparison" in anchors[identifier]["quote"]
                       for identifier in candidate["anchor_ids"])
        bounds = fitz.Rect(anchors[candidate["anchor_ids"][0]]["bbox"])
        for identifier in candidate["anchor_ids"][1:]:
            bounds |= fitz.Rect(anchors[identifier]["bbox"])
        assert list(bounds) == pytest.approx(candidate["bbox"], abs=.002)
        assert {fragment["anchor_id"] for fragment in candidate["layout"]} == set(candidate["anchor_ids"])
        assert all(fragment["spans"] for fragment in candidate["layout"])
    first, second = parsed["candidates"]
    assert "u" in first["text"] and "v" in first["text"] and "rZ" in first["text"]
    assert "rZ" in second["text"]
    assert first["bbox"][3] < second["bbox"][1]


def test_detached_operator_requires_real_math_before_accepting_number():
    from packages.paper_tools.parse import group_numbered_fragments
    def fragment(text, x, y, width=12):
        return {"text": text, "bbox": [x, y, x + width, y + 12], "font_size": 12}
    lines = [fragment("P", 180, 105), fragment("=", 194, 105), fragment("u", 208, 99),
             fragment("v", 208, 114), fragment("#(2)", 250, 105, 25),
             fragment("The value z = 3 is discussed (3)", 60, 165, 210),
             fragment("#(4)", 250, 220, 25)]
    candidates = group_numbered_fragments(lines)
    assert len(candidates) == 1
    assert candidates[0]["original_number"] == "2"
    assert set(candidates[0]["indexes"]) == {0, 1, 2, 3, 4}


def test_close_numbered_rows_do_not_consume_each_other():
    from packages.paper_tools.parse import group_numbered_fragments
    lines = [
        {"text": "x = y", "bbox": [100, 100, 140, 112], "font_size": 12},
        {"text": "(1)", "bbox": [450, 100, 470, 112], "font_size": 12},
        {"text": "a = b", "bbox": [100, 118, 140, 130], "font_size": 12},
        {"text": "(2)", "bbox": [450, 118, 470, 130], "font_size": 12},
    ]
    groups = group_numbered_fragments(lines)
    assert [group["original_number"] for group in groups] == ["1", "2"]
    assert groups[0]["text"] == "x = y"
    assert groups[1]["text"] == "a = b"
    assert groups[0]["indexes"] == [0, 1]
    assert groups[1]["indexes"] == [2, 3]


def test_close_numbered_rows_keep_original_fraction_block_together():
    from packages.paper_tools.parse import group_numbered_fragments
    lines = [
        {"text": "F =", "bbox": [100, 100, 121, 112], "font_size": 12, "text_block": 1},
        {"text": "u", "bbox": [122, 96, 129, 108], "font_size": 12, "text_block": 2},
        {"text": "v", "bbox": [122, 111, 129, 123], "font_size": 12, "text_block": 2},
        {"text": "#(1)", "bbox": [450, 100, 480, 112], "font_size": 12, "text_block": 3},
        {"text": "a = b", "bbox": [100, 118, 140, 130], "font_size": 12, "text_block": 4},
        {"text": "#(2)", "bbox": [450, 118, 480, 130], "font_size": 12, "text_block": 5},
    ]
    groups = group_numbered_fragments(lines)
    assert [group["original_number"] for group in groups] == ["1", "2"]
    assert groups[0]["indexes"] == [0, 1, 2, 3]
    assert groups[1]["indexes"] == [4, 5]
    assert groups[0]["bbox"] == [100, 96, 480, 123]


def test_two_equation_numbers_inside_one_pdf_text_block_stay_separate():
    from packages.paper_tools.parse import group_numbered_fragments
    lines = [
        {"text": "x = y #(1)", "bbox": [100, 100, 185, 112], "font_size": 12, "text_block": 1},
        {"text": "a = b #(2)", "bbox": [100, 118, 185, 130], "font_size": 12, "text_block": 1},
    ]
    groups = group_numbered_fragments(lines)
    assert [group["original_number"] for group in groups] == ["1", "2"]
    assert groups[0]["text"] == "x = y"
    assert groups[1]["text"] == "a = b"


def test_close_formula_rows_share_text_block_but_have_detached_numbers():
    from packages.paper_tools.parse import group_numbered_fragments
    lines = [
        {"text": "x = y", "bbox": [100, 100, 140, 112], "font_size": 12, "text_block": 1},
        {"text": "a = b", "bbox": [100, 118, 140, 130], "font_size": 12, "text_block": 1},
        {"text": "(1)", "bbox": [450, 100, 470, 112], "font_size": 12, "text_block": 2},
        {"text": "(2)", "bbox": [450, 118, 470, 130], "font_size": 12, "text_block": 3},
    ]
    groups = group_numbered_fragments(lines)
    assert [group["original_number"] for group in groups] == ["1", "2"]
    assert groups[0]["indexes"] == [0, 2]
    assert groups[1]["indexes"] == [1, 3]
