"""向量化索引 — text-embedding-v4 (DashScope 直连), 1024d"""
import os, hashlib, sys, time
from pathlib import Path
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-v4")
DIM = int(os.environ.get("EMBEDDING_DIM", "1024"))
REPOS = os.environ.get("REPOS_ROOT", "/repos")
QDRANT = os.environ.get("QDRANT_URL", "http://qdrant:6333")
API_KEY = os.environ.get("EMBEDDING_API_KEY", os.environ.get("DASHSCOPE_API_KEY", ""))
BASE_URL = os.environ.get("EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
COLLECTION = os.environ.get("QDRANT_COLLECTION", "codebase")

SKIP = {"node_modules", ".git", "__pycache__", "target", "dist", ".venv", "venv", "build", ".next", "vendor", "vendor_"}
EXT = {".py", ".ts", ".tsx", ".go", ".rs", ".java", ".js", ".jsx", ".vue", ".sql", ".yaml", ".toml", ".tf", ".lua", ".c", ".cpp", ".h", ".sh"}

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

print(f"Model: {MODEL}  dim={DIM}  Repos: {REPOS}  Qdrant: {QDRANT}")
sys.stdout.flush()

print("[1/3] Connecting to Qdrant...")
qdrant = QdrantClient(url=QDRANT)
if qdrant.collection_exists(COLLECTION):
    qdrant.delete_collection(COLLECTION)
qdrant.create_collection(COLLECTION, vectors_config=VectorParams(size=DIM, distance=Distance.COSINE))

print("[2/3] Scanning repos...")
chunks_batch = []  # (uid, rel, repo, start, end, lang)
text_batch = []
fcount, ccount = 0, 0

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
            chunks_batch.append((uid, rel, repo, start + 1, end, Path(fn).suffix.lstrip(".")))
            text_batch.append(chunk)
            ccount += 1

        fcount += 1
        if fcount % 100 == 0:
            print(f"  {fcount} files, {ccount} chunks...")
            sys.stdout.flush()

print(f"\n[3/3] Embedding {ccount} chunks via {MODEL} (max 30K chars/batch)...")

# 截断过长 chunk，避免单条过大导致 API 拒绝
MAX_CHUNK = 2000
for idx, t in enumerate(text_batch):
    if len(t) > MAX_CHUNK:
        text_batch[idx] = t[:MAX_CHUNK]

MAX_TOKENS_PER_BATCH = 30000
points, done, i = [], 0, 0
while i < len(text_batch):
    cur_texts, cur_meta = [], []
    cur_len = 0
    while i < len(text_batch) and len(cur_texts) < 10 and cur_len + len(text_batch[i]) < MAX_TOKENS_PER_BATCH:
        cur_texts.append(text_batch[i])
        cur_meta.append(chunks_batch[i])
        cur_len += len(text_batch[i])
        i += 1
    if not cur_texts:
        # 单条 chunk 超过 30K，强制截断后单条发送
        cur_texts = [text_batch[i][:MAX_TOKENS_PER_BATCH]]
        cur_meta = [chunks_batch[i]]
        i += 1
    resp = client.embeddings.create(model=MODEL, input=cur_texts, dimensions=DIM, encoding_format="float")
    for j, emb in enumerate(resp.data):
        uid, rel, repo, sl, el, lang = cur_meta[j]
        points.append(PointStruct(id=uid, vector=emb.embedding, payload={
            "repo": repo, "path": rel,
            "start_line": sl, "end_line": el,
            "language": lang,
        }))
    done += len(cur_texts)
    if len(points) >= 200:
        qdrant.upsert(collection_name=COLLECTION, points=points)
        points.clear()
    print(f"  {done}/{ccount} chunks embedded...")
    sys.stdout.flush()
    if done % 500 == 0:
        time.sleep(0.3)

if points:
    qdrant.upsert(collection_name=COLLECTION, points=points)

total = qdrant.count(collection_name=COLLECTION).count
print(f"\nDone! {fcount} files → {total} vectors in Qdrant ({COLLECTION})")
