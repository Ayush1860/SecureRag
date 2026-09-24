"""Application settings (pydantic-settings).

Every field can be set by an environment variable with the upper-cased field name
(``DATA_DIR``, ``CHROMA_DIR``, ``TOP_K``, ...) or in ``.env``. ``get_settings()`` returns a
cached instance; tests either construct ``Settings(...)`` directly or call
``get_settings.cache_clear()`` after changing the environment.
"""
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Ensure PyTorch is selected over TensorFlow in transformers/sentence-transformers
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")

# Secrets (AES key, provider API keys) are read from os.environ by other modules.
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    # Paths
    data_dir: str = "./data/sample"
    chroma_dir: str = "./data/chroma_db"
    audit_log_path: str = "./data/audit.jsonl"
    state_db_path: str = Field(default="", description="Ingestion state DB; default <chroma_dir>_state.sqlite")

    # Retrieval
    top_k: int = Field(default=5, ge=1, le=50)
    fusion_k: int = Field(default=60, ge=1)
    dense_candidates: int = Field(default=50, ge=1)
    sparse_candidates: int = Field(default=50, ge=1)
    rerank_enabled: bool = False
    context_token_budget: int = Field(default=3000, ge=100)

    # Embeddings and chunking
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embed_batch_size: int = Field(default=64, ge=1)
    embed_device: Literal["auto", "cpu", "cuda"] = "auto"
    chunk_size_tokens: int = Field(default=400, ge=16)
    chunk_overlap_tokens: int = Field(default=60, ge=0)
    ingest_batch_size: int = Field(default=512, ge=1)

    # Generation
    llm_provider: str = "mock"

    @model_validator(mode="after")
    def _check(self) -> "Settings":
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("chunk_overlap_tokens must be smaller than chunk_size_tokens")
        return self

    @property
    def resolved_state_db_path(self) -> str:
        if self.state_db_path:
            return self.state_db_path
        chroma = Path(self.chroma_dir)
        return str(chroma.with_name(chroma.name + "_state.sqlite"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
