"""向量化索引 — 在 chat-ui 容器内执行: python index_code.py"""
import os, hashlib, sys
from pathlib import Path
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from sentence_transformers import SentenceTransformer

MODEL = os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2")
REPOS = os.environ.get("REPOS_ROOT", "/repos")
QDRANT = os.environ.get("QDRANT_URL", "http://qdrant:6333")
COLLECTION = "jayli_code"
VEC_DIM = 384  # MiniLM outputs 384

SKIP = {"node_modules", ".git", "__pycache__", "target", "dist", ".venv", "venv", "build", ".next", "vendor", "vendor_"}
EXT = {".py", ".ts", ".tsx", ".go", ".rs", ".java", ".js", ".jsx", ".vue", ".sql", ".yaml", ".toml", ".tf", ".lua", ".c", ".cpp", ".h", ".sh"}

print(f"Model: {MODEL}  Repos: {REPOS}  Qdrant: {QDRANT}")
sys.stdout.flush()

print("[1/3] Loading embedding model...")
model = SentenceTransformer(MODEL, device="cpu")
print(f"      dim={model.get_sentence_embedding_dimension()}")

print("[2/3] Connecting to Qdrant...")
client = QdrantClient(url=QDRANT)
if client.collection_exists(COLLECTION):
    client.delete_collection(COLLECTION)
client.create_collection(COLLECTION, vectors_config=VectorParams(size=model.get_sentence_embedding_dimension(), distance=Distance.COSINE))

print("[3/3] Scanning repos...")
points, fcount, ccount = [], 0, 0
for root, dirs, files in os.walk(REPOS):
    dirs[:] = [d for d in dirs if d not in SKIP]
    for fn in files:
        if Path(fn).suffix not in EXT:
            continue
        fp = os.path.join(root, fn)
        rel = os.path.relpath(fp, REPOS)
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception:
            continue
        if not text.strip():
            continue
        repo = rel.split(os.sep)[0]
        lines = text.split("\n")
        for start in range(0, len(lines), 70):
            end = min(start + 80, len(lines))
            chunk = "\n".join(lines[start:end]).strip()
            if not chunk:
                continue
            uid = hashlib.md5(f"{rel}:{start+1}".encode()).hexdigest()
            vec = model.encode(chunk).tolist()
            points.append(PointStruct(id=uid, vector=vec, payload={
                "repo": repo, "path": rel,
                "start_line": start + 1, "end_line": end,
                "language": Path(fn).suffix.lstrip("."),
            }))
            ccount += 1

        fcount += 1
        if len(points) >= 100:
            client.upsert(collection_name=COLLECTION, points=points)
            points.clear()
        if fcount % 100 == 0:
            print(f"  {fcount} files, {ccount} chunks...")
            sys.stdout.flush()

if points:
    client.upsert(collection_name=COLLECTION, points=points)

total = client.count(collection_name=COLLECTION).count
print(f"\nDone! {fcount} files → {total} vectors in Qdrant")
