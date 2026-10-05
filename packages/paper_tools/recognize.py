from __future__ import annotations

from pathlib import Path

import fitz

from .common import open_pdf
from .errors import PaperToolError

GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon",
         "θ": "theta", "λ": "lambda", "μ": "mu", "π": "pi", "ρ": "rho", "σ": "sigma",
         "τ": "tau", "φ": "phi", "ω": "omega", "Δ": "Delta", "Σ": "Sigma", "Ω": "Omega"}


def text_to_latex(text: str) -> str:
    replacements = {"×": r"\cdot ", "·": r"\cdot ", "÷": "/", "−": "-", "≤": r"\leq ",
                    "≥": r"\geq ", "≈": r"\approx ", "∝": r"\propto ", "∞": r"\infty ",
                    "∑": r"\sum ", "∫": r"\int ", "√": r"\sqrt ", "²": "^{2}", "³": "^{3}"}
    for character, name in GREEK.items():
        replacements[character] = "\\" + name + " "
    return "".join(replacements.get(character, character) for character in text).strip()


def recognize_equations(parsed: dict, pdf_path: Path, output_dir: Path, settings) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    anchor_map = {source["id"]: source for source in parsed["anchors"]}
    equations = []
    model = None
    if settings.model_provider == "openai_compatible":
        from packages.providers.model import CompatibleModel
        model = CompatibleModel(settings)
    elif settings.model_provider != "heuristic":
        raise PaperToolError("MODEL_CONFIGURATION", "MODEL_PROVIDER 仅支持 heuristic 或 openai_compatible。")
    with open_pdf(pdf_path) as document:
        for index, candidate in enumerate(parsed["candidates"]):
            equation_id = f"EQ-{index + 1:02d}"
            sources = [anchor_map[identifier] for identifier in candidate["anchor_ids"]]
            rectangle = fitz.Rect(sources[0]["bbox"])
            for source in sources[1:]:
                rectangle |= fitz.Rect(source["bbox"])
            page = document[candidate["page"] - 1]
            old_rotation = page.rotation
            page.set_rotation(0)
            rectangle = (rectangle + (-5, -5, 5, 5)) & page.rect
            image_path = output_dir / f"{equation_id}.png"
            page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rectangle, alpha=False).save(image_path)
            page.set_rotation(old_rotation)
            recognition = {"status": "unknown", "provider": "heuristic", "confidence": None,
                           "reasons": ["文本规则仅生成 LaTeX 候选，无法验证上下标、分式及排版是否忠实。"]}
            latex = text_to_latex(candidate["text"])
            explanation = None
            if model:
                answer = model.recognize(candidate, sources, image_path)
                latex = answer["latex"]
                # A generated explanation is never promoted to a sourced factual
                # explanation without a subsequent explicit evidence check.
                recognition = {"status": "unknown", "provider": "openai_compatible", "confidence": None,
                               "reasons": answer["reasons"] + ["模型识别结果需要人工比对原文。"],
                               "model_uncertain": answer["uncertain"]}
            equations.append({"id": equation_id, "latex": latex, "original_text": candidate["text"],
                              "original_number": candidate["original_number"], "explanation": explanation,
                              "explanation_provenance": "unknown", "anchor_ids": candidate["anchor_ids"],
                              "symbols": [], "validation": {}, "status": "uncertain",
                              "recognition": recognition, "evidence_image": str(image_path),
                              "section_id": candidate["section_id"], "reading_order": candidate["reading_order"]})
    return equations
