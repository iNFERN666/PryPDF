import io
import re
from typing import Dict, List, Optional, Tuple

import streamlit as st
import fitz  # PyMuPDF


KG_PATTERN = re.compile(r"([\d.,]+)\s*KG", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"^[\d.,]+$")


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


def _gross_column_info(lines: List[Tuple[str, List[Dict]]]) -> Optional[Tuple[float, float]]:
    for line_text, spans in lines:
        lower = line_text.lower()
        if "gross" in lower and "weight" in lower:
            x0s = []
            x1s = []
            for span in spans:
                txt = span.get("text", "").strip().lower()
                if "gross" in txt or "weight" in txt:
                    x0, _, x1, _ = span["bbox"]
                    x0s.append(x0)
                    x1s.append(x1)
            if x0s and x1s:
                left = min(x0s)
                right = max(x1s)
                center = (left + right) / 2
                width = max(right - left, 40)
                return center, width
    return None


def _draw_text(page: fitz.Page, span: Dict, text: str) -> None:
    x0, y0, x1, y1 = span["bbox"]
    rect = fitz.Rect(x0, y0, x1, y1)
    page.draw_rect(rect, color=None, fill=(1, 1, 1))

    fontname = span.get("font", "helv")
    fontsize = span.get("size", 10)
    try:
        page.insert_textbox(
            rect,
            text,
            fontname=fontname,
            fontsize=fontsize,
            color=(0, 0, 0),
            align=fitz.TEXT_ALIGN_RIGHT,
        )
    except Exception:
        page.insert_textbox(
            rect,
            text,
            fontname="helv",
            fontsize=fontsize,
            color=(0, 0, 0),
            align=fitz.TEXT_ALIGN_RIGHT,
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
    col_center: float,
    col_width: float,
) -> int:
    replaced = 0
    tol = max(30, col_width * 1.5)

    for line_text, spans in lines:
        lower = line_text.lower()
        if "gross" in lower and "weight" in lower:
            continue
        if "net" in lower and "weight" in lower:
            continue

        for idx, span in enumerate(spans):
            x0, _, x1, _ = span["bbox"]
            center = (x0 + x1) / 2
            if abs(center - col_center) > tol:
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


def _replace_in_page(page: fitz.Page, add_kg: float) -> int:
    lines = _get_lines(page)
    col_info = _gross_column_info(lines)
    if col_info:
        col_center, col_width = col_info
        return _replace_by_column(page, lines, add_kg, col_center, col_width)
    return _replace_by_first_kg_in_line(page, lines, add_kg)


def process_pdf(data: bytes, add_kg: float) -> Tuple[bytes, int]:
    doc = fitz.open(stream=data, filetype="pdf")
    total_replaced = 0
    for page in doc:
        total_replaced += _replace_in_page(page, add_kg)

    out = io.BytesIO()
    doc.save(out, deflate=True, clean=True)
    doc.close()
    return out.getvalue(), total_replaced


st.set_page_config(page_title="PryPDF", layout="centered")

st.title("PryPDF – Gross Weight Updater")
st.write(
    "Încarcă un PDF (Delivery Note / Packing List), introduce adaosul în KG "
    "și primești PDF-ul modificat cu Gross Weight incrementat."
)

add_kg = st.number_input("Adaos (KG)", min_value=0.0, step=0.001, format="%.3f")
file = st.file_uploader("Încarcă PDF", type=["pdf"])

if file and st.button("Procesează"):
    data = file.read()
    with st.spinner("Procesez PDF-ul..."):
        output, replaced = process_pdf(data, add_kg)
    st.success(f"Gata. Am actualizat {replaced} valori de Gross Weight.")
    st.download_button(
        "Descarcă PDF modificat",
        data=output,
        file_name="packing_list_updated.pdf",
        mime="application/pdf",
    )

st.caption(
    "Notă: Pentru a păstra aspectul paginii, aplicația rescrie doar valorile "
    "Gross Weight în același loc, cu aliniere la dreapta."
)
