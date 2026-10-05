"""Synchronous deterministic document-tool facade used by workflow/MCP."""
from pathlib import Path

from .errors import PaperToolError
from .export import export_result
from .inspect import inspect_document
from .normalize import normalize_document
from .parse import extract_document_blocks
from .recognize import recognize_equations
from .relate import build_equation_relations
from .resolve import resolve_symbols
from .validate import validate_result


class PaperTools:
    def __init__(self, settings):
        self.settings = settings

    def inspect(self, path: Path, mime: str) -> dict:
        return inspect_document(Path(path), mime, self.settings)

    def normalize(self, path: Path, mime: str, output_dir: Path) -> dict:
        return normalize_document(Path(path), mime, Path(output_dir), self.settings)

    def parse(self, pdf_path: Path, rendition_id: str, document_version: int = 1) -> dict:
        return extract_document_blocks(Path(pdf_path), rendition_id, document_version)

    def recognize(self, parsed: dict, pdf_path: Path, output_dir: Path) -> list[dict]:
        return recognize_equations(parsed, Path(pdf_path), Path(output_dir), self.settings)

    def resolve(self, equations: list[dict], parsed: dict) -> list[dict]:
        return resolve_symbols(equations, parsed)

    def relate(self, equations: list[dict], parsed: dict) -> list[dict]:
        return build_equation_relations(equations, parsed)

    def validate(self, result: dict) -> dict:
        return validate_result(result)

    def export(self, result: dict, pdf_path: Path, output_dir: Path) -> dict:
        return export_result(result, Path(pdf_path), Path(output_dir))


__all__ = ["PaperTools", "PaperToolError"]
