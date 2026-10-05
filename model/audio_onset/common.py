"""Small helpers shared by train.py, golden.py and gen_rom.py (standard library only)."""
import hashlib, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SPEC_PATH = os.path.join(HERE, "spec.json")
WEIGHTS_PATH = os.path.join(HERE, "weights.json")


def spec():
    return json.load(open(SPEC_PATH))


def weights():
    return json.load(open(WEIGHTS_PATH))


def sha256_of(*paths):
    h = hashlib.sha256()
    for p in paths:
        h.update(os.path.relpath(p, REPO).encode() + b"\0" + open(p, "rb").read())
    return h.hexdigest()


def write_if_changed(path, text):
    """Write only when the content differs (keeps timestamps and `make check-generated` quiet)."""
    old = open(path).read() if os.path.exists(path) else None
    if old == text:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)
    return True


def clean_label(w, margin):
    """Ground truth (the examples, NOT the model): louder if newest two minus oldest two >= margin."""
    return int((w[2] + w[3]) - (w[0] + w[1]) >= margin)
