import shutil

REQUIRED_BINARIES = ["kubectl"]
REQUIRED_MODULES = []

def check_dependencies():
    missing = []
    for binary in REQUIRED_BINARIES:
        if not shutil.which(binary):
            missing.append(f"❌ Missing required binary: {binary}")
    for module in REQUIRED_MODULES:
        try:
            __import__(module)
        except ImportError:
            missing.append(f"❌ Missing required Python module: {module}")
    return missing
