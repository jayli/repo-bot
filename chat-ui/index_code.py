"""向量化索引 — text-embedding-v4 (DashScope 直连), 1024d"""
import os, hashlib, sys, time, argparse
from pathlib import Path
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

def generate_id(rel_path: str, start_line: int) -> str:
    """生成与已存在向量一致的 UUID 格式 ID"""
    md5_hash = hashlib.md5(f"{rel_path}:{start_line}".encode()).hexdigest()
    # 转换为 UUID 格式：8-4-4-4-12
    return f"{md5_hash[:8]}-{md5_hash[8:12]}-{md5_hash[12:16]}-{md5_hash[16:20]}-{md5_hash[20:]}"

parser = argparse.ArgumentParser(description="向量化索引脚本")
parser.add_argument("--incremental", action="store_true", help="增量模式：跳过已存在的向量")
args = parser.parse_args()

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
print(f"Mode: {'增量 (跳过已存在)' if args.incremental else '全量 (重建 collection)'}")
sys.stdout.flush()

print("[1/3] Connecting to Qdrant...")
qdrant = QdrantClient(url=QDRANT)

if args.incremental:
    # 增量模式：如果 collection 不存在则创建，否则保留现有数据
    if not qdrant.collection_exists(COLLECTION):
        print(f"  Collection '{COLLECTION}' 不存在，创建新 collection...")
        qdrant.create_collection(COLLECTION, vectors_config=VectorParams(size=DIM, distance=Distance.COSINE))
    else:
        existing = qdrant.count(collection_name=COLLECTION).count
        print(f"  Collection '{COLLECTION}' 已存在，包含 {existing} 个向量")
else:
    # 全量模式：删除并重建
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
            uid = generate_id(rel, start + 1)
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

# 增量模式：获取已存在的向量 ID 集合
existing_ids = set()
if args.incremental and qdrant.collection_exists(COLLECTION):
    print("  扫描已存在的向量 ID...")
    sys.stdout.flush()
    offset = None
    while True:
        result = qdrant.scroll(collection_name=COLLECTION, limit=1000, offset=offset, with_payload=False, with_vectors=False)
        points, next_offset = result
        for p in points:
            existing_ids.add(str(p.id))
        if next_offset is None:
            break
        offset = next_offset
    print(f"  已存在 {len(existing_ids)} 个向量，将跳过这些 ID")
    sys.stdout.flush()

# 过滤掉已存在的向量（增量模式）
skipped = 0
if args.incremental and existing_ids:
    filtered_chunks = []
    filtered_texts = []
    for i in range(len(chunks_batch)):
        uid = chunks_batch[i][0]
        if uid not in existing_ids:
            filtered_chunks.append(chunks_batch[i])
            filtered_texts.append(text_batch[i])

    skipped = len(chunks_batch) - len(filtered_chunks)
    print(f"  跳过 {skipped} 个已存在向量，剩余 {len(filtered_chunks)} 个需要处理")
    sys.stdout.flush()

    chunks_batch = filtered_chunks
    text_batch = filtered_texts
    ccount = len(text_batch)

MAX_TOKENS_PER_BATCH = 30000
points = []
done = 0
i = 0
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

    # 重试逻辑：DashScope API 偶尔返回瞬时错误
    for attempt in range(5):
        try:
            resp = client.embeddings.create(model=MODEL, input=cur_texts, dimensions=DIM, encoding_format="float")
            break
        except Exception as e:
            if attempt == 4:
                raise RuntimeError(f"Embedding API 连续 5 次失败，最后一批: {cur_meta[0]}") from e
            wait = 2 ** attempt  # 1, 2, 4, 8 秒
            print(f"  ⚠️ Embedding API 错误 (attempt {attempt+1}/5): {e}, {wait}s 后重试...")
            sys.stdout.flush()
            time.sleep(wait)

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
if args.incremental:
    print(f"  新增: {done - skipped}, 跳过: {skipped}")
