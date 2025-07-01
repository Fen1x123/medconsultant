# ------------- MedConsultant v7 (с защитой паролем и секретами) -------------
import streamlit as st
import os, io, base64, datetime, json, tempfile, uuid, subprocess, glob
import openai
from PyPDF2 import PdfReader
import docx2txt
from docx import Document
from docx.shared import Pt, RGBColor
from docx2pdf import convert
from PIL import Image
import pydicom

# ——————————————————————————————
# 1) Авторизация по паролю
# ——————————————————————————————
APP_PASSWORD = st.secrets.get("APP_PASSWORD") or os.getenv("APP_PASSWORD")
pwd = st.sidebar.text_input("Введите пароль для доступа", type="password")
if pwd != APP_PASSWORD:
    st.sidebar.error("🔒 Неверный пароль")
    st.stop()

# ——————————————————————————————
# 2) Инициализация OpenAI‑ключа из Secrets или окружения
# ——————————————————————————————
openai.api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    st.error("❗ Не задан OPENAI_API_KEY. Добавьте его в Secrets или переменную окружения.")
    st.stop()

openai_client = openai.OpenAI()

# ——————————————————————————————
# Конфигурация модели и UI
# ——————————————————————————————
MODEL       = "gpt-4o-mini"
TEMPERATURE = 0.35
MAX_TOKENS  = 1800
THEME       = RGBColor(0, 102, 204)

st.set_page_config("MedConsultant 🩺", page_icon="🩺", layout="wide")
st.title("🩺 MedConsultant — медицинский эксперт‑консультант")

# ——————————————————————————————
# Данные пациента
# ——————————————————————————————
st.sidebar.header("🧑‍⚕️ Данные пациента")
patient_name = st.sidebar.text_input("ФИО пациента")
patient_age  = st.sidebar.text_input("Возраст")
patient_sex  = st.sidebar.selectbox("Пол", ["", "М", "Ж"])

# ——————————————————————————————
# Состояние сессии: загруженные файлы
# ——————————————————————————————
if "files" not in st.session_state:
    st.session_state.files = {}

# ——————————————————————————————
# Загрузка файлов
# ——————————————————————————————
uploaded = st.file_uploader(
    "Загрузите мед‑файлы (PDF, DOCX, DICOM, изображения)",
    accept_multiple_files=True
)
if uploaded:
    for f in uploaded:
        st.session_state.files[f.name] = {"data": f.read(), "note": ""}

# ——————————————————————————————
# Внутренние заметки (для ИИ)
# ——————————————————————————————
if st.session_state.files:
    st.subheader("📁 Файлы и заметки (только для анализа)")
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

# ——————————————————————————————
# Пары функций-утилит
# ——————————————————————————————
def extract_text(fname: str, data: bytes):
    ext = fname.lower().split('.')[-1]
    if ext == "pdf":
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    if ext in ("docx", "doc"):
        return docx2txt.process(io.BytesIO(data))
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
    header = (
        f"Пациент: {patient_name or '—'}, {patient_age or '—'} лет, пол: {patient_sex or '—'}.\n"
        "Вы — ведущий врач‑консультант с 20‑летним стажем.\n"
        "Составьте заключение строго по структуре:\n"
        "1) Проведённые анализы\n"
        "2) Заключение по анализам\n"
        "3) Назначенное лечение (дозы, кратность, длительность)\n"
        "4) Рекомендации (образ жизни, доп. обследования, сроки контроля)\n"
        "Не используйте маркеры, эмодзи, CAPS‑заголовки, «обратитесь» или «ИИ».\n"
    )

    text_block = ""
    vision_blocks = []
    for fname, meta in st.session_state.files.items():
        extracted = extract_text(fname, meta["data"])
        note = meta["note"].strip()
        if isinstance(extracted, dict):
            if note:
                vision_blocks.append({"type":"text", "text": note})
            vision_blocks.append({
                "type":"image_url",
                "image_url": {"url": "data:image/png;base64," + extracted["img"]}
            })
        else:
            text_block += f"\n=== {fname} ===\n"
            if note:
                text_block += f"Комментарий врача: {note}\n"
            text_block += extracted[:15000] + "\n"

    if global_note:
        text_block = "Глобальные указания: " + global_note + "\n" + text_block

    messages = [
        {"role":"system", "content": header},
        {"role":"user",   "content": text_block.strip() or "нет данных"}
    ]
    if vision_blocks:
        messages.append({"role":"user", "content": vision_blocks})

    res = openai_client.chat.completions.create(
        model=MODEL, messages=messages,
        temperature=TEMPERATURE, max_tokens=MAX_TOKENS
    )
    return res.choices[0].message.content.strip()

def build_docx(text: str) -> bytes:
    doc = Document()
    p = doc.add_paragraph(); r = p.add_run("Медицинское заключение")
    r.bold = True; r.font.size = Pt(22); r.font.color.rgb = THEME
    doc.add_paragraph(f"{datetime.datetime.now():%d.%m.%Y %H:%M}")
    doc.add_paragraph(f"{patient_name or '—'}, {patient_age or '—'} лет, пол: {patient_sex or '—'}")
    doc.add_paragraph().add_run("═" * 40)

    for line in text.splitlines():
        doc.add_paragraph(line.strip())

    buf = io.BytesIO(); doc.save(buf)
    return buf.getvalue()

def docx_to_pdf(docx_bytes: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        docx_path = os.path.join(tmp, f"{uuid.uuid4()}.docx")
        with open(docx_path, "wb") as f:
            f.write(docx_bytes)
        convert(docx_path, tmp)
        pdf_path = glob.glob(os.path.join(tmp, "*.pdf"))[0]
        return open(pdf_path, "rb").read()

# ——————————————————————————————
# Генерация и скачивание отчёта
# ——————————————————————————————
def generate_report(to_pdf: bool):
    if not st.session_state.files:
        st.warning("⚠️ Загрузите хотя бы один файл.")
        return
    with st.spinner("ИИ анализирует материалы…"):
        result = integrated_analysis()

    docx_bytes = build_docx(result)
    if to_pdf:
        try:
            pdf_bytes = docx_to_pdf(docx_bytes)
            st.download_button(
                "⬇️ Скачать PDF‑отчёт",
                pdf_bytes, "MedConsultant_Report.pdf",
                mime="application/pdf"
            )
        except Exception as e:
            st.error(f"PDF не создан: {e}")
    else:
        st.download_button(
            "⬇️ Скачать DOCX‑отчёт",
            docx_bytes, "MedConsultant_Report.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

col1, col2 = st.columns(2)
with col1:
    if st.button("📄 DOCX‑отчёт"):
        generate_report(False)
with col2:
    if st.button("📑 PDF‑отчёт"):
        generate_report(True)
