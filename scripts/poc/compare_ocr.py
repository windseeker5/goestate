"""Compare Docling and GLM-OCR on the same PDF files.

This isolated experiment does not import Flask, open the estate database,
modify uploaded files, or change the production ingestion pipeline.

Examples:
    venv/bin/python scripts/poc/compare_ocr.py instance/uploads/file.pdf
    venv/bin/python scripts/poc/compare_ocr.py instance/uploads/*.pdf --limit 3

Docling always runs. Ollama runs when ``--ollama-model`` is provided. GLM-OCR
is available as an optional comparison with ``--glm``.
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", nargs="+", type=Path, help="PDF files to compare")
    parser.add_argument(
        "--output", type=Path, default=Path("test/ocr-comparison"),
        help="Output directory (default: test/ocr-comparison)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Process only the first N PDFs")
    parser.add_argument("--term", action="append", default=[], help="Term to count; repeatable")
    parser.add_argument("--ollama-model", help="Ollama vision model, for example qwen2.5vl:3b")
    parser.add_argument("--glm", action="store_true", help="Also run the GLM-OCR Python SDK")
    parser.add_argument("--require-glm", action="store_true", help="Fail if GLM-OCR is unavailable")
    return parser.parse_args()


def extract_docling(pdf_path):
    from docling.document_converter import DocumentConverter

    started = time.perf_counter()
    document = DocumentConverter().convert(str(pdf_path)).document
    return document.export_to_markdown(), time.perf_counter() - started


def extract_glmocr(pdf_path):
    from glmocr import parse

    started = time.perf_counter()
    result = parse(str(pdf_path))
    structured = getattr(result, "json_result", None)
    text = getattr(result, "markdown", None)
    if text is None and isinstance(structured, str):
        text = structured
    if text is None:
        text = json.dumps(structured, ensure_ascii=False, indent=2, default=str)
    if not text:
        raise RuntimeError("GLM-OCR returned no readable text")
    return text, time.perf_counter() - started, structured


def extract_ollama(pdf_path, model):
    import pymupdf
    import requests

    started = time.perf_counter()
    pages = []
    with pymupdf.open(pdf_path) as document:
        for page_number, page in enumerate(document, start=1):
            image = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False).tobytes("png")
            encoded = base64.b64encode(image).decode("ascii")
            response = requests.post(
                "http://127.0.0.1:11434/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "options": {"temperature": 0},
                    "messages": [{
                        "role": "user",
                        "content": (
                            "Transcribe this document page exactly as Markdown. "
                            "Preserve French accents, names, dates, amounts, identifiers, "
                            "and table structure. Do not summarize or invent missing text."
                        ),
                        "images": [encoded],
                    }],
                },
                timeout=300,
            )
            response.raise_for_status()
            content = clean_markdown(response.json()["message"]["content"])
            pages.append(f"## Page {page_number}\n\n{content.strip()}")
    return "\n\n".join(pages), time.perf_counter() - started


def count_terms(text, terms):
    lowered = text.casefold()
    return ", ".join(f"{term}={lowered.count(term.casefold())}" for term in terms) or "-"


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def clean_markdown(text):
    """Remove a model's outer Markdown code fence before Typora renders it."""
    lines = text.strip().splitlines()
    if len(lines) >= 2 and lines[0].strip().lower() in {"```", "```markdown"} and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text.strip()


def main():
    args = parse_args()
    pdfs = [path for path in args.pdfs if path.suffix.casefold() == ".pdf"]
    missing = [path for path in pdfs if not path.is_file()]
    if missing:
        for path in missing:
            print(f"Missing PDF: {path}", file=sys.stderr)
        return 1
    if args.limit > 0:
        pdfs = pdfs[:args.limit]
    if not pdfs:
        print("No PDF files selected.", file=sys.stderr)
        return 1

    try:
        import glmocr  # noqa: F401
        glm_available = True
    except ImportError:
        glm_available = False
        if args.require_glm:
            print("GLM-OCR is not installed. Install it with: pip install glmocr", file=sys.stderr)
            return 1

    args.output.mkdir(parents=True, exist_ok=True)
    print(f"Comparing {len(pdfs)} PDF(s)")
    print(f"Outputs: {args.output}")
    print(f"Ollama: {args.ollama_model or 'skipped'}")
    print(f"GLM-OCR: {'available' if glm_available and args.glm else 'skipped'}\n")
    print("file | parser | seconds | chars | table-lines | terms")
    print("--- | --- | ---: | ---: | ---: | ---")

    failures = 0
    for pdf_path in pdfs:
        stem = pdf_path.stem
        try:
            text, seconds = extract_docling(pdf_path)
            write_text(args.output / f"{stem}.docling.md", text)
            print(f"{pdf_path.name} | Docling | {seconds:.2f} | {len(text)} | {text.count('|')} | {count_terms(text, args.term)}")
        except Exception as exc:  # noqa: BLE001 - report per-file experiment failures
            failures += 1
            print(f"{pdf_path.name} | Docling FAILED | {exc}")

        if args.ollama_model:
            try:
                text, seconds = extract_ollama(pdf_path, args.ollama_model)
                text = clean_markdown(text)
                write_text(args.output / f"{stem}.ollama.md", text)
                print(f"{pdf_path.name} | Ollama/{args.ollama_model} | {seconds:.2f} | {len(text)} | {text.count('|')} | {count_terms(text, args.term)}")
            except Exception as exc:  # noqa: BLE001 - report per-file experiment failures
                failures += 1
                print(f"{pdf_path.name} | Ollama FAILED | {exc}")

        if args.glm and glm_available:
            try:
                text, seconds, structured = extract_glmocr(pdf_path)
                write_text(args.output / f"{stem}.glmocr.md", text)
                if structured is not None:
                    (args.output / f"{stem}.glmocr.json").write_text(
                        json.dumps(structured, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
                    )
                print(f"{pdf_path.name} | GLM-OCR | {seconds:.2f} | {len(text)} | {text.count('|')} | {count_terms(text, args.term)}")
            except Exception as exc:  # noqa: BLE001 - report per-file experiment failures
                failures += 1
                print(f"{pdf_path.name} | GLM-OCR FAILED | {exc}")

    print("\nRead the saved .docling.md and .glmocr.md files side by side.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
