import io
import re
from typing import Dict, List, Tuple

import streamlit as st
import fitz  # PyMuPDF


PATTERN = re.compile(
    r"(RO\d+)\s+(.+?KM)\s+([\d.,]+)\s*KG\s+([\d.,]+)\s*KG",
    re.IGNORECASE,
)


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


def _collect_spans(page: fitz.Page) -> List[Dict]:
    spans = []
    text_dict = page.get_text("dict")
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                spans.append(span)
    return spans


def _find_span_for_text(spans: List[Dict], text: str, used: set) -> Dict | None:
    for i, span in enumerate(spans):
        if text in span.get("text", "") and i not in used:
            used.add(i)
            return span
    return None


def _replace_in_page(page: fitz.Page, add_kg: float) -> int:
    page_text = page.get_text("text")
    spans = _collect_spans(page)
    used_spans = set()
    replaced = 0

    for match in PATTERN.finditer(page_text):
        gross_text = match.group(3)
        value, sep, decimals = _parse_number(gross_text)
        new_value = value + add_kg
        new_text = _format_number(new_value, sep, decimals)

        span = _find_span_for_text(spans, gross_text, used_spans)
        if not span:
            continue

        x0, y0, x1, y1 = span["bbox"]
        rect = fitz.Rect(x0, y0, x1, y1)

        # White-out old text and write new text in same box, right-aligned.
        page.draw_rect(rect, color=None, fill=(1, 1, 1))

        fontname = span.get("font", "helv")
        fontsize = span.get("size", 10)
        try:
            page.insert_textbox(
                rect,
                new_text,
                fontname=fontname,
                fontsize=fontsize,
                color=(0, 0, 0),
                align=fitz.TEXT_ALIGN_RIGHT,
            )
        except Exception:
            page.insert_textbox(
                rect,
                new_text,
                fontname="helv",
                fontsize=fontsize,
                color=(0, 0, 0),
                align=fitz.TEXT_ALIGN_RIGHT,
            )

        replaced += 1

    return replaced


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
