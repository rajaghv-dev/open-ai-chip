def pytest_configure(config):
    config.addinivalue_line("markers", "live: needs a local Ollama model; opt-in with HERMES_LIVE=1")
