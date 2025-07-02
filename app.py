# ------------- MedConsultant v9 (Финальная тестовая версия) -------------
import io, base64, datetime, json, os, sys, tempfile, uuid, subprocess, glob, re
import streamlit as st
import openai
import fitz  # PyMuPDF
import docx
from docx.shared import Pt, RGBColor
from docx2pdf import convert
from PIL import Image
import pydicom

# ---------- Инициализация API‑ключа ----------
# Ищем сначала в Streamlit Secrets, потом — в переменной окружения
openai.api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    st.error("❗ Не задан OPENAI_API_KEY. Задайте секрет в Streamlit Cloud или переменную окружения.")
    st.stop()

# Создаём клиент OpenAI (будет использовать openai.api_key)
openai_client = openai.OpenAI()

# ---------- Конфигурация модели ----------
# Используем экономную модель для тестирования, чтобы избежать ошибок с лимитами
MODEL       = "gpt-4o-mini"
TEMPERATURE = 0.35
MAX_TOKENS  = 4000
THEME       = RGBColor(0, 102, 204)

# ---------- UI ----------
st.set_page_config("MedConsultant 🩺", page_icon="🩺", layout="wide")
st.title("🩺 MedConsultant — медицинский эксперт‑консультант")

st.sidebar.header("🧑‍⚕️ Данные пациента")
patient_name = st.sidebar.text_input("ФИО пациента")
patient_age  = st.sidebar.text_input("Возраст")
patient_sex  = st.sidebar.selectbox("Пол", ["", "М", "Ж"])

# ---------- Сессия ----------
if "files" not in st.session_state:
    st.session_state.files = {}

# ---------- Хелпер: Авто-определение даты ----------
def extract_date_from_file(fname: str, data: bytes) -> datetime.date | None:
    """Пытается извлечь дату сначала из имени файла, затем из метаданных."""
    patterns = [
        r'(\d{4})[._-](\d{2})[._-](\d{2})', # 2025-07-02
        r'(\d{2})[._-](\d{2})[._-](\d{4})'  # 02-07-2025
    ]
    for pattern in patterns:
        match = re.search(pattern, fname)
        if match:
            try:
                parts = match.groups()
                if len(parts[0]) == 4: year, month, day = map(int, parts)
                else: day, month, year = map(int, parts)
                return datetime.date(year, month, day)
            except ValueError:
                continue

    ext = fname.lower().split('.')[-1]
    try:
        if ext == "pdf":
            with fitz.open(stream=data, filetype="pdf") as doc:
                meta = doc.metadata
                date_str = meta.get('creationDate') or meta.get('modDate')
                if date_str: return datetime.datetime.strptime(date_str[2:10], "%Y%m%d").date()
        elif ext == "docx":
            with io.BytesIO(data) as docx_io:
                doc = docx.Document(docx_io)
                if doc.core_properties.created: return doc.core_properties.created.date()
        elif ext == "dcm":
             with io.BytesIO(data) as dcm_io:
                dcm = pydicom.dcmread(dcm_io)
                date_str = dcm.get("StudyDate") or dcm.get("AcquisitionDate")
                if date_str: return datetime.datetime.strptime(date_str, "%Y%m%d").date()
    except Exception:
        return None
    return None

# ---------- Загрузка файлов ----------
uploaded = st.file_uploader(
    "Загрузите мед‑файлы (PDF, DOCX, изображения, DICOM…)",
    accept_multiple_files=True
)
if uploaded:
    for f in uploaded:
        if f.name not in st.session_state.files:
            file_data = f.read()
            guessed_date = extract_date_from_file(f.name, file_data) or datetime.date.today()
            st.session_state.files[f.name] = {"data": file_data, "note": "", "date": guessed_date}

# ---------- Файлы, заметки и даты (с ручной корректировкой) ----------
if st.session_state.files:
    st.subheader("📁 Файлы, заметки и даты анализов")
    st.info("ℹ️ Дата для каждого файла определяется автоматически. Вы можете исправить её вручную.")

    sorted_files = sorted(st.session_state.files.items(), key=lambda item: item[1]['date'])
    st.session_state.files = dict(sorted_files)

    for fname, meta in st.session_state.files.items():
        with st.expander(f"{fname} (дата: {meta['date']:%d.%m.%Y})", expanded=False):
            col1, col2 = st.columns(2)
            with col1:
                meta["date"] = st.date_input("Дата анализа", value=meta["date"], key=f"date_{fname}")
            with col2:
                meta["note"] = st.text_area("Заметка к файлу", meta["note"], key=f"note_{fname}")

st.markdown("---")
global_note = st.text_area("📝 Общие указания для анализа", placeholder="Например: уделите внимание функции почек...")

# ---------- Хелперы обработки файлов ----------
def process_file(fname: str, data: bytes) -> dict:
    """Извлекает текст и СЖАТЫЕ ИЗОБРАЖЕНИЯ из разных форматов файлов."""
    ext = fname.lower().split('.')[-1]
    text_content = ""
    images_base64 = []

    def resize_and_encode(img_bytes: bytes) -> str:
        """Сжимает изображение для экономии токенов."""
        try:
            with Image.open(io.BytesIO(img_bytes)) as img:
                img.thumbnail((1024, 1024))
                buf = io.BytesIO()
                # PNG лучше для графиков и текста, JPEG - для фото. Используем PNG как универсальный.
                img_format = "PNG"
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.save(buf, format="JPEG", quality=85) # JPEG дает лучшее сжатие
                return base64.b64encode(buf.getvalue()).decode('utf-8')
        except Exception:
            # Если сжать не удалось, кодируем как есть
            return base64.b64encode(img_bytes).decode('utf-8')

    try:
        if ext == "pdf":
            doc = fitz.open(stream=data, filetype="pdf")
            for page in doc:
                text_content += page.get_text() + "\n"
                for img_instance in page.get_images(full=True):
                    xref = img_instance[0]
                    base_image = doc.extract_image(xref)
                    images_base64.append(resize_and_encode(base_image["image"]))
            doc.close()
        elif ext == "docx":
            doc = docx.Document(io.BytesIO(data))
            for para in doc.paragraphs: text_content += para.text + "\n"
            for rel in doc.part.rels.values():
                if "image" in rel.target_ref:
                    images_base64.append(resize_and_encode(rel.target_part.blob))
        elif ext in ("txt", "csv", "md"):
            text_content = data.decode(errors="ignore")
        elif ext == "dcm":
            dcm = pydicom.dcmread(io.BytesIO(data))
            text_content = str(dcm)
            if hasattr(dcm, "pixel_array"):
                img = Image.fromarray(dcm.pixel_array)
                buf = io.BytesIO(); img.save(buf, format="PNG");
                images_base64.append(resize_and_encode(buf.getvalue()))
        elif ext in ("png", "jpg", "jpeg", "tiff", "bmp", "gif"):
            images_base64.append(resize_and_encode(data))
    except Exception as e:
        st.warning(f"Не удалось полностью обработать файл {fname}: {e}")
    return {"text": text_content, "images": images_base64}

def integrated_analysis() -> str:
    """Собираем все данные и отправляем запрос к модели."""
    header = (
        f"Пациент: {patient_name or '—'}, {patient_age or '—'} лет, пол: {patient_sex or '—'}.\n"
        "Вы — ведущий врач‑консультант с 20‑летним стажем. Проведите глубокий анализ предоставленных данных, включая изображения и динамику по датам.\n"
        "СФОРМИРУЙТЕ ЗАКЛЮЧЕНИЕ СТРОГО ПО СТРУКТУРЕ:\n"
        "1) Проведённые исследования\n"
        "2) Заключение по результатам (с анализом динамики и изображений)\n"
        "3) Предварительный диагноз/гипотеза\n"
        "4) План лечения (препараты, дозы, длительность)\n"
        "5) Рекомендации (образ жизни, доп. обследования, сроки контроля).\n"
        "ЗАПРЕЩЕНО: Использовать маркеры, эмодзи, CAPS, фразы «обратитесь к врачу», упоминания ИИ."
    )
    messages = [{"role": "system", "content": header}]
    user_content = []

    if global_note.strip():
        user_content.append({"type": "text", "text": f"Общие указания: {global_note.strip()}"})

    for fname, meta in st.session_state.files.items():
        file_date = meta['date'].strftime('%d.%m.%Y')
        file_note = meta['note'].strip()
        file_header = f"=== Анализ файла: {fname} (дата: {file_date}) ===\n"
        if file_note: file_header += f"Комментарий: {file_note}\n"

        processed = process_file(fname, meta["data"])
        file_text = processed['text'].strip()

        if file_text: user_content.append({"type": "text", "text": file_header + file_text[:20000]})
        else: user_content.append({"type": "text", "text": file_header + "(Текст отсутствует)"})

        for img_b64 in processed['images']:
            user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}})

    if not user_content: user_content.append({"type": "text", "text": "Нет данных."})
    messages.append({"role": "user", "content": user_content})

    resp = openai_client.chat.completions.create(
        model=MODEL, messages=messages, temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
    return resp.choices[0].message.content.strip()

def build_docx(text: str) -> bytes:
    doc = docx.Document()
    p = doc.add_paragraph(); r = p.add_run("Медицинское заключение")
    r.bold = True; r.font.size = Pt(22); r.font.color.rgb = THEME
    doc.add_paragraph(f"{datetime.datetime.now():%d.%m.%Y %H:%M}")
    doc.add_paragraph(f"Пациент: {patient_name or '—'}, {patient_age or '—'} лет, пол: {patient_sex or '—'}")
    doc.add_paragraph().add_run("═" * 40)
    for line in text.splitlines(): doc.add_paragraph(line.strip())
    buf = io.BytesIO(); doc.save(buf)
    return buf.getvalue()

def docx_to_pdf(docx_bytes: bytes) -> bytes | None:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            docx_path = os.path.join(tmp, f"{uuid.uuid4()}.docx")
            with open(docx_path, "wb") as f: f.write(docx_bytes)
            convert(docx_path, tmp)
            pdf_paths = glob.glob(os.path.join(tmp, "*.pdf"))
            if not pdf_paths: raise FileNotFoundError("PDF не создан.")
            return open(pdf_paths[0], "rb").read()
    except Exception as e:
        st.error(f"Ошибка конвертации в PDF: {e}")
        return None

# ---------- Генерация отчёта ----------
def generate_report():
    if not st.session_state.files:
        st.warning("⚠️ Загрузите хотя бы один файл для анализа.")
        return

    with st.spinner("ИИ проводит комплексный анализ... Это может занять до минуты..."):
        result_text = integrated_analysis()

    st.success("Анализ завершен!")
    st.markdown("### Сгенерированный отчёт:")
    st.text_area("Текст отчёта", result_text, height=600, key="result_text_area")

    docx_bytes = build_docx(result_text)
    st.session_state.docx_bytes = docx_bytes
    pdf_bytes = docx_to_pdf(docx_bytes)
    if pdf_bytes: st.session_state.pdf_bytes = pdf_bytes


st.markdown("---")
if st.button("🚀 Сгенерировать отчёт", type="primary"):
    generate_report()

if "docx_bytes" in st.session_state:
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "⬇️ Скачать DOCX", st.session_state.docx_bytes,
            f"MedReport_{patient_name or 'P'}.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    if "pdf_bytes" in st.session_state:
        with col2:
            st.download_button(
                "⬇️ Скачать PDF", st.session_state.pdf_bytes,
                f"MedReport_{patient_name or 'P'}.pdf", "application/pdf"
            )