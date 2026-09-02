import runpy
from pathlib import Path

target = Path(__file__).resolve().parent / "app" / "streamlit_app.py"
runpy.run_path(str(target), run_name="__main__")
