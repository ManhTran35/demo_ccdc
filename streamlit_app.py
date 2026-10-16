"""Streamlit Community Cloud: a read-only Drive dashboard or synthetic demo."""
import os
import runpy
from pathlib import Path
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

# Keep this separate from app.py's local and authenticated GCS deployments.
try:
    source = st.secrets.get("data_source", "google_drive" if "drive" in st.secrets else "demo")
except (FileNotFoundError, StreamlitSecretNotFoundError):
    source = "demo"
source = os.environ.get("CCDC_DATA_SOURCE", source)
source = source if isinstance(source, str) else "invalid"
os.environ["APP_ENV"] = {"demo": "community-demo", "google_drive": "community-drive"}.get(source, "invalid")
os.environ["STORAGE_BACKEND"] = "local"
runpy.run_path(str(Path(__file__).with_name("app.py")), run_name="__main__")
