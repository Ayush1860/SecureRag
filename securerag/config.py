import os
from dataclasses import dataclass
from dotenv import load_dotenv

# Ensure PyTorch is selected over TensorFlow in transformers/sentence-transformers
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")

load_dotenv()


@dataclass(frozen=True)
class Settings:
    data_dir: str = os.getenv("DATA_DIR", "./data/sample")
    chroma_dir: str = os.getenv("CHROMA_DIR", "./data/chroma_db")
    audit_log_path: str = os.getenv("AUDIT_LOG_PATH", "./data/audit.jsonl")
    top_k: int = int(os.getenv("TOP_K", "5"))
    fusion_k: int = int(os.getenv("FUSION_K", "60"))
    llm_provider: str = os.getenv("LLM_PROVIDER", "mock")
