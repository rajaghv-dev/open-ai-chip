# conftest.py -- pytest configuration shared by every file in tests/tools: registers the `live` marker used by the opt-in
# tests that need a local Ollama model (HERMES_LIVE=1). No tests live here and nothing else is configured.
# Run: build/agent/venv/bin/python -m pytest -q tests/tools   (see tests/tools/TEST_MATRIX_TOOLS.md)
# Docs: tests/tools/TEST_MATRIX_TOOLS.md
def pytest_configure(config):
    config.addinivalue_line("markers", "live: needs a local Ollama model; opt-in with HERMES_LIVE=1")


import os as _os
import tempfile as _tempfile

# job tests must not write to the real run history (build/agent/memory/runs.md): the tool server's job-completion hook
# records into CHIP_MEMORY_DIR, which is a throw-away directory here unless a test sets its own.
_os.environ.setdefault("CHIP_MEMORY_DIR", _tempfile.mkdtemp(prefix="chip_mem_"))
