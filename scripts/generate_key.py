import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from securerag.security.encryption import VectorStoreEncryptor

print(VectorStoreEncryptor().export_key_b64())
