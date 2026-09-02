from pathlib import Path


def load_tagged_documents(root: str):
    root_path = Path(root)
    for path in root_path.glob("*/*/*.txt"):
        department = path.parent.parent.name
        clearance = path.parent.name
        yield path.read_text(encoding="utf-8"), {"department": department, "clearance": clearance, "source": path.name}


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks
