# PryPDF

Aplicație Streamlit pentru actualizarea automată a valorilor **Gross Weight** în PDF‑uri de tip Delivery Note / Packing List, păstrând layout‑ul paginii.

## Funcționalitate
- Upload PDF
- Introduci adaos în KG
- Aplicația găsește câmpurile **Gross Weight** și le incrementează
- Descarci PDF-ul modificat

## Rulare locală (Docker)
```bash
docker compose up --build
```
Accesează: http://localhost:8501

## Deploy pe Streamlit Community Cloud
Repository-ul conține:
- `app.py`
- `requirements.txt`

Poți face deploy direct din GitHub pe:
`https://share.streamlit.io/deploy`

## Note
Aplicația modifică doar valorile Gross Weight și încearcă să păstreze fontul, alinierea și poziția textului.
