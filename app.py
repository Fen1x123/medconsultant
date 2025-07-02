# ------------- MedConsultant v7 PRO (улучшенная версия) -------------
import io, base64, datetime, json, os, sys, tempfile, uuid, subprocess, glob
import streamlit as st
import openai
import fitz  # PyMuPDF для лучшей экстракции PDF
import docx2txt
from docx import Document
from docx.shared import Pt, RGBColor
from docx2pdf import convert
from PIL import Image
import pydicom

# ---------- Инициализация API‑ключа ----------
openai.api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    st.error("❗ Не задан OPENAI_API_KEY. Задайте секрет в Streamlit Cloud или переменную окружения.")
    st.stop()

openai_client = openai.OpenAI()

# ---------- Конфигурация ----------
THEME = RGBColor(0, 102, 204)
DEFAULT_MODEL = "gpt-4o"
MODEL_LIST = ["gpt-4o", "gpt-4o-mini", "gpt-4.0", "gpt-3.5-turbo"]

st.set_page_config("MedConsultant 🩺", page_icon="🩺", layout="wide")
st.title("🩺 MedConsultant — медицинский эксперт‑консультант")

st.sidebar.header("🧑‍⚕️ Данные пациента")
patient_name = st.sidebar.text_input("ФИО пациента")
patient_age = st.sidebar.text_input("Возраст")
patient_sex = st.sidebar.selectbox("Пол", ["", "М", "Ж"])
model = st.sidebar.selectbox("Модель LLM", MODEL_LIST, index=MODEL_LIST.index(DEFAULT_MODEL))
max_tokens = st.sidebar.slider("Максимум токенов на ответ", 1024, 4096, 3072, step=256)
temperature = st.sidebar.slider("Temperature", 0.0, 1.2, 0.35, step=0.05)

if "files" not in st.session_state:
    st.session_state.files = {}

uploaded = st.file_uploader(
    "Загрузите мед‑файлы (PDF, DOCX, изображения, DICOM…)",
    accept_multiple_files=True
)
if uploaded:
    for f in uploaded:
        st.session_state.files[f.name] = {"data": f.read(), "note": ""}

if st.session_state.files:
    st.subheader("📁 Файлы и внутренние заметки (для ИИ)")
    for fname, meta in st.session_state.files.items():
        with st.expander(fname, expanded=False):
            meta["note"] = st.text_area(
                "Заметка (НЕ попадёт в отчёт)",
                meta["note"],
                key=f"note_{fname}"
            )

st.markdown("---")
global_note = st.text_area(
    "📝 Общие указания для анализа (НЕ попадут в отчёт)",
    placeholder="Например: уделите внимание функции почек…"
)

# ---------- Хелперы ----------
def clean_text(text: str) -> str:
    """Очистка текста от мусора и повторов"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # убираем повторяющиеся разделители и лишние шапки
    out = []
    for line in lines:
        if line and not line.startswith("Страница") and not line.startswith("—"*5):
            out.append(line)
    return "\n".join(out)

def extract_text(fname: str, data: bytes):
    ext = fname.lower().split('.')[-1]
    if ext == "pdf":
        # Сначала PyMuPDF
        try:
            doc = fitz.open("pdf", data)
            text = ""
            for page in doc:
                text += page.get_text()
            if not text.strip():
                raise Exception("No text with fitz")
            return clean_text(text)
        except Exception:
            # Fallback на PyPDF2
            from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(data))
            return clean_text("\n".join(page.extract_text() or "" for page in reader.pages))
    if ext in ("docx", "doc"):
        return clean_text(docx2txt.process(io.BytesIO(data)))
    if ext in ("txt", "csv", "md"):
        return data.decode(errors="ignore")
    if ext == "dcm":
        dcm = pydicom.dcmread(io.BytesIO(data))
        if hasattr(dcm, "pixel_array"):
            img = Image.fromarray(dcm.pixel_array)
            buf = io.BytesIO(); img.save(buf, format="PNG"); data = buf.getvalue()
    if ext in ("png", "jpg", "jpeg", "tiff", "bmp", "gif"):
        return {"img": base64.b64encode(data).decode()}
    return ""

def integrated_analysis() -> str:
    """Собираем все файлы, заметки и отправляем запрос к модели."""
    header = (
        f"Пациент: {patient_name or '—'}, {patient_age or '—'} лет, пол: {patient_sex or '—'}.\n"
        "Вы — ведущий врач‑консультант с 20‑летним стажем.\n"
        "Составьте заключение строго по структуре:\n"
        "1) Проведённые анализы\n"
        "2) Заключение по анализам\n"
        "3) Назначенное лечение (дозы, кратность, длительность)\n"
        "4) Рекомендации (образ жизни, доп. обследования, сроки контроля)\n"
        "Не используйте маркеры, эмодзи, заголовки CAPS, фразы «обратитесь» или упоминания ИИ.\n"
    )

    # Сборка промпта — все в одном user‑блоке
    content_blocks = [{"type": "text", "text": header}]
    for fname, meta in st.session_state.files.items():
        extracted = extract_text(fname, meta["data"])
        note = meta["note"].strip()
        if isinstance(extracted, dict):  # изображение
            if note:
                content_blocks.append({"type": "text", "text": f"Комментарий к изображению ({fname}): {note}"})
            content_blocks.append({
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64," + extracted["img"],
                    "detail": "high"
                }
            })
        else:
            block = f"\n=== {fname} ===\n"
            if note:
                block += f"Комментарий врача: {note}\n"
            block += extracted[:16000] + "\n"
            content_blocks.append({"type": "text", "text": block})

    if global_note:
        content_blocks.insert(1, {"type": "text", "text": "Глобальные указания: " + global_note})

    messages = [
        {"role": "user", "content": content_blocks}
    ]

    resp = openai_client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens
    )
    return resp.choices[0].message.content.strip()

def build_docx(text: str) -> bytes:
    doc = Document()
    p = doc.add_paragraph()
    r = p.add_run("Медицинское заключение")
    r.bold = True; r.font.size = Pt(22); r.font.color.rgb = THEME
    doc.add_paragraph(f"{datetime.datetime.now():%d.%m.%Y %H:%M}")
    doc.add_paragraph(f"{patient_name or '—'}, {patient_age or '—'} лет, пол: {patient_sex or '—'}")
    doc.add_paragraph().add_run("═" * 40)
    for line in text.splitlines():
        doc.add_paragraph(line.strip())
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()

def docx_to_pdf(docx_bytes: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        docx_path = os.path.join(tmp, f"{uuid.uuid4()}.docx")
        with open(docx_path, "wb") as f:
            f.write(docx_bytes)
        convert(docx_path, tmp)
        pdf_path = glob.glob(os.path.join(tmp, "*.pdf"))[0]
        return open(pdf_path, "rb").read()

def generate_report(to_pdf: bool):
    if not st.session_state.files:
        st.warning("⚠️ Загрузите хотя бы один файл для анализа.")
        return
    with st.spinner("ИИ анализирует материалы…"):
        result_text = integrated_analysis()
    docx_bytes = build_docx(result_text)
    if to_pdf:
        try:
            pdf_bytes = docx_to_pdf(docx_bytes)
            st.download_button(
                "⬇️ Скачать PDF‑отчёт",
                pdf_bytes,
                "MedConsultant_Report.pdf",
                mime="application/pdf"
            )
        except Exception as e:
            st.error(f"PDF не создан: {e}")
    else:
        st.download_button(
            "⬇️ Скачать DOCX‑отчёт",
            docx_bytes,
            "MedConsultant_Report.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

col1, col2 = st.columns(2)
with col1:
    if st.button("📄 DOCX‑отчёт"):
        generate_report(False)
with col2:
    if st.button("📑 PDF‑отчёт"):
        generate_report(True)
