"""Optional offline conversion container; /work contains input.docx and output/."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: converter <filename.docx> (file must be in /work)")
    name = sys.argv[1]
    if Path(name).name != name or Path(name).suffix.lower() != ".docx":
        raise SystemExit("Only a DOCX filename without directory components is accepted")
    root = Path("/work").resolve()
    source = (root / name).resolve()
    if source.parent != root or not source.is_file():
        raise SystemExit("Input file not found in /work")
    output = root / "output"
    output.mkdir(exist_ok=True)
    timeout = int(os.environ.get("CONVERSION_TIMEOUT", "90"))
    with tempfile.TemporaryDirectory(prefix="paper2formula-") as profile:
        try:
            subprocess.run(
                ["soffice", f"-env:UserInstallation={Path(profile).as_uri()}",
                 "--headless", "--convert-to", "pdf", "--outdir", str(output), str(source)],
                check=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise SystemExit("DOCX conversion exceeded the configured timeout") from None
    target = output / source.with_suffix(".pdf").name
    if not target.is_file() or target.stat().st_size == 0:
        raise SystemExit("LibreOffice did not produce a PDF")
    print(f"Converted PDF: {target}")


if __name__ == "__main__":
    main()
