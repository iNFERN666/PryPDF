import io
import re
import zipfile
from typing import Dict, List, Optional, Tuple
from pathlib import Path

import streamlit as st
import fitz  # PyMuPDF


KG_PATTERN = re.compile(r"([\d.,]+)\s*KG", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"^[\d.,]+$")
MAX_FILE_MB = 20
MAX_PAGES = 300


def _parse_number(num_str: str) -> Tuple[float, str, int]:
    """Return (value, separator, decimals) based on input string."""
    sep = "," if "," in num_str else "."
    if sep in num_str:
        decimals = len(num_str.split(sep)[1])
    else:
        decimals = 0
    value = float(num_str.replace(",", "."))
    return value, sep, decimals


def _format_number(value: float, sep: str, decimals: int) -> str:
    fmt = f"{{:.{decimals}f}}"
    s = fmt.format(value)
    if sep == ",":
        s = s.replace(".", ",")
    return s


def _get_lines(page: fitz.Page) -> List[Tuple[str, List[Dict]]]:
    lines: List[Tuple[str, List[Dict]]] = []
    text_dict = page.get_text("dict")
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            line_text = "".join(span.get("text", "") for span in spans)
            lines.append((line_text, spans))
    return lines


def _find_header_center(
    lines: List[Tuple[str, List[Dict]]], keywords: Tuple[str, str]
) -> Optional[Tuple[float, float, float, float]]:
    for line_text, spans in lines:
        lower = line_text.lower()
        if all(k in lower for k in keywords):
            x0s = []
            x1s = []
            y1s = []
            for span in spans:
                txt = span.get("text", "").strip().lower()
                if any(k in txt for k in keywords):
                    x0, _, x1, y1 = span["bbox"]
                    x0s.append(x0)
                    x1s.append(x1)
                    y1s.append(y1)
            if x0s and x1s:
                left = min(x0s)
                right = max(x1s)
                center = (left + right) / 2
                width = max(right - left, 40)
                bottom = max(y1s) if y1s else 0.0
                return center, width, left, bottom
    return None


def _draw_text(page: fitz.Page, span: Dict, text: str) -> None:
    x0, y0, x1, y1 = span["bbox"]
    fontname = span.get("font", "helv")
    fontsize = span.get("size", 10)
    origin = span.get("origin", (x0, y1))
    asc = span.get("ascender", 0.8)
    desc = span.get("descender", -0.2)

    try:
        new_width = fitz.get_text_length(text, fontname=fontname, fontsize=fontsize)
        old_width = fitz.get_text_length(
            span.get("text", ""), fontname=fontname, fontsize=fontsize
        )
        use_font = fontname
    except Exception:
        use_font = "helv"
        new_width = fitz.get_text_length(text, fontname=use_font, fontsize=fontsize)
        old_width = fitz.get_text_length(
            span.get("text", ""), fontname=use_font, fontsize=fontsize
        )

    max_width = max(new_width, old_width)
    x_end = x1
    x_start = x_end - max_width
    y_baseline = origin[1]
    y_top = y_baseline - fontsize * asc
    y_bottom = y_baseline - fontsize * desc

    pad_x = max(0.2, max_width * 0.02)
    pad_y = max(0.2, (y_bottom - y_top) * 0.08)
    rect = fitz.Rect(x_start - pad_x, y_top - pad_y, x_end + pad_x, y_bottom + pad_y)
    page.draw_rect(rect, color=None, fill=(1, 1, 1))

    page.insert_text(
        (x_end - new_width, y_baseline),
        text,
        fontname=use_font,
        fontsize=fontsize,
        color=(0, 0, 0),
    )


def _update_span_number(span: Dict, add_kg: float) -> Optional[str]:
    text = span.get("text", "")
    match = KG_PATTERN.search(text)
    if match:
        num_str = match.group(1)
        value, sep, decimals = _parse_number(num_str)
        new_num = _format_number(value + add_kg, sep, decimals)
        return text.replace(num_str, new_num, 1)

    if NUMBER_PATTERN.match(text.strip()):
        value, sep, decimals = _parse_number(text.strip())
        return _format_number(value + add_kg, sep, decimals)

    return None


def _replace_by_column(
    page: fitz.Page,
    lines: List[Tuple[str, List[Dict]]],
    add_kg: float,
    gross_center: float,
    net_center: Optional[float],
    col_width: float,
    header_bottom: Optional[float],
) -> int:
    replaced = 0
    if net_center is None:
        return 0
    split = (gross_center + net_center) / 2
    gross_is_left = gross_center < net_center
    min_y = (header_bottom or 0.0) + 2.0 if header_bottom else None

    if min_y is None:
        # derive a safe top boundary from first gross-column KG occurrence
        candidate_top = None
        for line_text, spans in lines:
            lower = line_text.lower()
            if "gross" in lower and "weight" in lower:
                continue
            if "net" in lower and "weight" in lower:
                continue
            for idx, span in enumerate(spans):
                x0, _, x1, _ = span["bbox"]
                center = (x0 + x1) / 2
                if gross_is_left and center >= split:
                    continue
                if not gross_is_left and center <= split:
                    continue
                text = span.get("text", "")
                if not text.strip():
                    continue
                is_kg = KG_PATTERN.search(text)
                if not is_kg and NUMBER_PATTERN.match(text.strip()):
                    next_span = spans[idx + 1] if idx + 1 < len(spans) else None
                    if not (next_span and "kg" in next_span.get("text", "").lower()):
                        continue
                y_top = span["bbox"][1]
                if candidate_top is None or y_top < candidate_top:
                    candidate_top = y_top
        if candidate_top is not None:
            min_y = candidate_top - 1.0

    for line_text, spans in lines:
        lower = line_text.lower()
        if "gross" in lower and "weight" in lower:
            continue
        if "net" in lower and "weight" in lower:
            continue

        for idx, span in enumerate(spans):
            x0, _, x1, _ = span["bbox"]
            center = (x0 + x1) / 2
            if gross_is_left and center >= split:
                continue
            if not gross_is_left and center <= split:
                continue
            if min_y is not None and span["bbox"][1] <= min_y:
                continue

            text = span.get("text", "")
            if not text.strip():
                continue

            if KG_PATTERN.search(text):
                new_text = _update_span_number(span, add_kg)
                if new_text:
                    _draw_text(page, span, new_text)
                    replaced += 1
                continue

            if NUMBER_PATTERN.match(text.strip()):
                next_span = spans[idx + 1] if idx + 1 < len(spans) else None
                if next_span and "kg" in next_span.get("text", "").lower():
                    new_text = _update_span_number(span, add_kg)
                    if new_text:
                        _draw_text(page, span, new_text)
                        replaced += 1

    return replaced


def _replace_by_first_kg_in_line(
    page: fitz.Page, lines: List[Tuple[str, List[Dict]]], add_kg: float
) -> int:
    replaced = 0
    for line_text, spans in lines:
        lower = line_text.lower()
        if "gross" in lower and "weight" in lower:
            continue
        if "net" in lower and "weight" in lower:
            continue

        occurrences = []
        for idx, span in enumerate(spans):
            text = span.get("text", "")
            if KG_PATTERN.search(text):
                occurrences.append((idx, "kg_in_span"))
            elif NUMBER_PATTERN.match(text.strip()):
                next_span = spans[idx + 1] if idx + 1 < len(spans) else None
                if next_span and "kg" in next_span.get("text", "").lower():
                    occurrences.append((idx, "num_only"))

        if not occurrences:
            continue

        idx, _ = occurrences[0]
        span = spans[idx]
        new_text = _update_span_number(span, add_kg)
        if new_text:
            _draw_text(page, span, new_text)
            replaced += 1

    return replaced


def _replace_in_page(
    page: fitz.Page,
    add_kg: float,
    cached: Dict[str, Optional[float]],
) -> Tuple[int, Dict[str, Optional[float]]]:
    lines = _get_lines(page)
    gross_info = _find_header_center(lines, ("gross", "weight"))
    net_info = _find_header_center(lines, ("net", "weight"))

    if gross_info and net_info:
        gross_center, col_width, _, gross_bottom = gross_info
        cached["gross_center"] = gross_center
        cached["net_center"] = net_info[0]
        cached["col_width"] = col_width
        cached["header_bottom"] = gross_bottom

        return (
            _replace_by_column(
                page,
                lines,
                add_kg,
                gross_center,
                cached["net_center"],
                col_width,
                gross_bottom,
            ),
            cached,
        )

    if cached.get("gross_center") is not None and cached.get("net_center") is not None:
        return (
            _replace_by_column(
                page,
                lines,
                add_kg,
                cached["gross_center"],
                cached["net_center"],
                cached.get("col_width") or 40,
                None,
            ),
            cached,
        )

    return 0, cached


def process_pdf(data: bytes, add_kg: float) -> Tuple[bytes, int]:
    doc = fitz.open(stream=data, filetype="pdf")
    if doc.is_encrypted:
        raise ValueError("PDF-ul este criptat și nu poate fi procesat.")
    if doc.page_count > MAX_PAGES:
        raise ValueError(f"PDF-ul are prea multe pagini (max {MAX_PAGES}).")
    total_replaced = 0
    cached: Dict[str, Optional[float]] = {
        "gross_center": None,
        "net_center": None,
        "col_width": None,
        "header_bottom": None,
    }
    for page in doc:
        replaced, cached = _replace_in_page(page, add_kg, cached)
        total_replaced += replaced

    out = io.BytesIO()
    doc.save(out, deflate=True, clean=True)
    doc.close()
    return out.getvalue(), total_replaced


def run_app() -> None:
    st.set_page_config(page_title="PryPDF", layout="centered")

    st.title("PryPDF – Gross Weight Updater")
    st.write(
        "Încarcă un PDF (Delivery Note / Packing List), introduce adaosul în KG "
        "și primești PDF-ul modificat cu Gross Weight incrementat."
    )

    add_kg = st.number_input("Adaos (KG)", min_value=0.0, step=0.001, format="%.3f")
    files = st.file_uploader("Încarcă PDF", type=["pdf"], accept_multiple_files=True)

    if files and st.button("Procesează"):
        results: List[Tuple[str, bytes, int]] = []
        errors: List[str] = []

        with st.spinner("Procesez PDF-urile..."):
            for file in files:
                if file.type != "application/pdf":
                    errors.append(f"{file.name}: fișierul nu este PDF valid.")
                    continue
                if getattr(file, "size", 0) > MAX_FILE_MB * 1024 * 1024:
                    errors.append(
                        f"{file.name}: depășește limita de {MAX_FILE_MB} MB."
                    )
                    continue

                data = file.read()
                try:
                    output, replaced = process_pdf(data, add_kg)
                except ValueError as exc:
                    errors.append(f"{file.name}: {exc}")
                    continue

                results.append((Path(file.name).name, output, replaced))

        if errors:
            for msg in errors:
                st.error(msg)

        if results:
            total = sum(r[2] for r in results)
            st.success(
                f"Gata. Am actualizat {total} valori de Gross Weight în {len(results)} fișier(e)."
            )

            if len(results) > 1:
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for name, data, _ in results:
                        zf.writestr(name, data)
                st.download_button(
                    "Descarcă toate (ZIP)",
                    data=zip_buffer.getvalue(),
                    file_name="packing_list_updated.zip",
                    mime="application/zip",
                )

            for name, data, _ in results:
                st.download_button(
                    f"Descarcă {name}",
                    data=data,
                    file_name=name,
                    mime="application/pdf",
                )

    st.caption(
        "Notă: Pentru a păstra aspectul paginii, aplicația rescrie doar valorile "
        "Gross Weight în același loc, cu aliniere la dreapta."
    )


if __name__ == "__main__":
    run_app()
