# run.py
import streamlit.web.cli as stcli
import sys, os, threading, webbrowser

# Находим app.py в сборке или рядом
if getattr(sys, "frozen", False):
    base = sys._MEIPASS
    app_path = os.path.join(base, "app.py")
else:
    app_path = "app.py"

# Запускаем Streamlit в «продакшн‑режиме» на порту 8501
sys.argv = [
    "streamlit", "run", app_path,
    "--server.headless", "true",
    "--global.developmentMode", "false"
]

# Открываем браузер через 1.5 секунды
def _open_browser():
    webbrowser.open("http://localhost:8501")

threading.Timer(1.5, _open_browser).start()

# Стартуем
sys.exit(stcli.main())
