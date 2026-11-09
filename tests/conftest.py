"""Run every test against a throwaway data folder, so tests never touch a real feed or price cache."""
import os
import tempfile

os.environ["FINN_DATA_DIR"] = tempfile.mkdtemp(prefix="finn-test-")
