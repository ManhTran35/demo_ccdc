"""Streamlit Community Cloud entry point for the synthetic, read-only demo."""
import os
import runpy
from pathlib import Path

# Keep this separate from app.py's local and authenticated GCS deployments.
os.environ["APP_ENV"] = "community-demo"
os.environ["STORAGE_BACKEND"] = "local"
runpy.run_path(str(Path(__file__).with_name("app.py")), run_name="__main__")
