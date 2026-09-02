import os
import sys
from pathlib import Path

# Ensure project root is on sys.path for direct script execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from securerag.config import Settings
from securerag.retrieval.store import build_store
from securerag.security.encryption import VectorStoreEncryptor

load_dotenv()
settings = Settings()
encryptor = VectorStoreEncryptor()

print(f"Starting SecureRAG ingestion from: {settings.data_dir}")
_, collection, _, chunks = build_store(settings.data_dir, settings.chroma_dir, encryptor)

print(f"Successfully indexed {len(chunks)} encrypted chunks into ChromaDB at {settings.chroma_dir}")

key_b64 = encryptor.export_key_b64()
if encryptor.is_ephemeral:
    print("\n" + "!" * 70)
    print("WARNING: SECURERAG_AES_KEY_B64 was not found in .env.")
    print("A random 32-byte ephemeral key was generated for this ingestion batch.")
    print("To allow the API server to decrypt these chunks across restarts, add this to .env:")
    print(f"SECURERAG_AES_KEY_B64={key_b64}")
    print("!" * 70 + "\n")
else:
    print(f"[OK] Persisted chunks using configured AES-256 key.")
