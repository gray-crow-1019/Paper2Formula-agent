from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import fitz

from .common import DOCX_MIME, PDF_MIME, sha256
from .errors import PaperToolError
from .inspect import inspect_document


def normalize_document(path: Path, mime: str, output_dir: Path, settings) -> dict:
    inspected = inspect_document(path, mime, settings)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "normalized.pdf"
    if destination.resolve() == path.resolve():
        raise PaperToolError("SOURCE_MISMATCH", "规范化输出不能覆盖原文件。")
    converter_version = f"PyMuPDF {fitz.VersionBind}; PDF passthrough"
    if mime == PDF_MIME:
        shutil.copyfile(path, destination)
    elif mime == DOCX_MIME:
        configured = str(settings.libreoffice_path or "soffice")
        executable = shutil.which(configured)
        if not executable and Path(configured).is_file():
            executable = str(Path(configured).resolve())
        if not executable:
            raise PaperToolError("CONVERTER_UNAVAILABLE", "未找到 LibreOffice，请在 .env 配置 LIBREOFFICE_PATH。")
        try:
            version = subprocess.run([executable, "--version"], capture_output=True, text=True,
                                     timeout=10, check=False, shell=False)
            converter_version = version.stdout.strip()[:200] or "LibreOffice (version unavailable)"
            with tempfile.TemporaryDirectory(prefix="paper2formula-convert-", dir=output_dir) as temporary:
                sandbox = Path(temporary)
                input_file = sandbox / "source.docx"
                shutil.copyfile(path, input_file)
                converted = sandbox / "converted"
                converted.mkdir()
                profile = sandbox / "profile"
                environment = os.environ.copy()
                # Do not pass model credentials or application secrets to the converter.
                environment = {key: value for key, value in environment.items()
                               if not any(marker in key.upper() for marker in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))}
                arguments = [executable, f"-env:UserInstallation={profile.resolve().as_uri()}",
                             "--headless", "--nologo", "--nodefault", "--nofirststartwizard",
                             "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(converted), str(input_file)]
                completed = subprocess.run(arguments, cwd=sandbox, env=environment,
                                           capture_output=True, timeout=settings.conversion_timeout,
                                           check=False, shell=False)
                source_pdf = converted / "source.pdf"
                if completed.returncode or not source_pdf.is_file():
                    raise PaperToolError("CONVERSION_FAILED", "LibreOffice 未成功生成 PDF。")
                inspect_document(source_pdf, PDF_MIME, settings)
                shutil.copyfile(source_pdf, destination)
        except subprocess.TimeoutExpired as exc:
            raise PaperToolError("CONVERSION_TIMEOUT", "DOCX 转换超时。", True) from exc
        except OSError as exc:
            raise PaperToolError("CONVERSION_FAILED", "无法启动或完成 LibreOffice 转换。") from exc
        inspected = inspect_document(destination, PDF_MIME, settings)
    return {"pdf_path": str(destination), "sha256": sha256(destination),
            "original_sha256": inspected["sha256"] if mime == PDF_MIME else sha256(path),
            "converter_version": converter_version, "page_count": inspected["page_count"],
            "pages": inspected["pages"]}
