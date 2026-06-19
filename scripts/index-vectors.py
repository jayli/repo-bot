#!/usr/bin/env python3
"""
代码向量化索引脚本
遍历 REPOS_ROOT 下所有仓库，用 tree-sitter AST 切片后做 Embedding 写入 Qdrant
"""
import os
import sys
import json
import hashlib
import argparse
from pathlib import Path

# chromadb is an optional fallback, imported only when --backend chroma is used

# === 配置 ===
REPOS_ROOT = os.path.expanduser(os.environ.get("REPOS_ROOT", "~/projects"))
EXTENSIONS = {".py", ".ts", ".tsx", ".go", ".rs", ".java", ".js", ".jsx",
              ".vue", ".sql", ".yaml", ".toml", ".tf", ".lua", ".c", ".cpp", ".h"}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "target", "dist",
             ".venv", "venv", "build", ".next", "vendor"}


def iter_files(root: str):
    """遍历所有代码文件"""
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if Path(f).suffix in EXTENSIONS:
                yield os.path.join(dirpath, f)


def chunk_file(file_path: str, max_lines: int = 80, overlap: int = 10) -> list[dict]:
    """简单按行切片（TODO: 替换为 tree-sitter AST 切片）"""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return []

    lines = content.split("\n")
    chunks = []
    start = 0
    while start < len(lines):
        end = min(start + max_lines, len(lines))
        text = "\n".join(lines[start:end])
        if text.strip():
            chunks.append({
                "text": text,
                "start_line": start + 1,
                "end_line": end,
            })
        start += max_lines - overlap
    return chunks


def build_qdrant_index():
    """写入 Qdrant"""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct
    from sentence_transformers import SentenceTransformer

    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    collection = os.environ.get("QDRANT_COLLECTION", "codebase")

    print(f"[1/3] 加载 Embedding 模型 (bge-m3)...")
    model = SentenceTransformer("BAAI/bge-m3")

    print(f"[2/3] 连接 Qdrant: {qdrant_url}")
    client = QdrantClient(url=qdrant_url)

    # 重建 collection
    client.recreate_collection(
        collection_name=collection,
        vectors_config=VectorParams(
            size=1024,  # bge-m3 输出 1024 维
            distance=Distance.COSINE,
        ),
    )

    print(f"[3/3] 扫描仓库: {REPOS_ROOT}")
    points = []
    batch_size = 200
    file_count = 0

    for fp in iter_files(REPOS_ROOT):
        rel = os.path.relpath(fp, REPOS_ROOT)
        repo = rel.split(os.sep)[0]
        chunks = chunk_file(fp)
        for ch in chunks:
            uid = hashlib.md5(f"{rel}:{ch['start_line']}".encode()).hexdigest()
            embedding = model.encode(ch["text"]).tolist()
            points.append(PointStruct(
                id=uid,
                vector=embedding,
                payload={
                    "repo": repo,
                    "path": rel,
                    "start_line": ch["start_line"],
                    "end_line": ch["end_line"],
                    "language": Path(fp).suffix.lstrip("."),
                },
            ))

        file_count += 1
        if len(points) >= batch_size:
            client.upsert(collection_name=collection, points=points[:batch_size])
            points = points[batch_size:]
            print(f"  ...{file_count} 文件已处理")

        if file_count % 500 == 0:
            print(f"  进度: {file_count} 文件, {client.count(collection_name=collection).count} 向量")

    # 最后一批
    if points:
        client.upsert(collection_name=collection, points=points)

    total = client.count(collection_name=collection).count
    print(f"\n完成! {file_count} 个文件, {total} 条向量记录")


def build_chroma_index():
    """写入 Chroma（轻量备选）"""
    from chromadb.config import Settings
    from chromadb.utils import embedding_functions

    coll_name = os.environ.get("QDRANT_COLLECTION", "codebase")
    db_path = os.path.expanduser("~/.repo-bot/chroma_db")
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="BAAI/bge-m3",
        device="cpu",
    )
    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_or_create_collection(
        name=coll_name,
        embedding_function=ef,
    )

    print(f"扫描: {REPOS_ROOT}")
    ids, documents, metadatas = [], [], []
    batch_size = 200
    file_count = 0

    for fp in iter_files(REPOS_ROOT):
        rel = os.path.relpath(fp, REPOS_ROOT)
        repo = rel.split(os.sep)[0]
        for ch in chunk_file(fp):
            ids.append(hashlib.md5(f"{rel}:{ch['start_line']}".encode()).hexdigest())
            documents.append(ch["text"])
            metadatas.append({
                "repo": repo, "path": rel,
                "start_line": ch["start_line"], "end_line": ch["end_line"],
                "language": Path(fp).suffix.lstrip("."),
            })

        file_count += 1
        if len(ids) >= batch_size:
            collection.add(ids=ids, documents=documents, metadatas=metadatas)
            ids, documents, metadatas = [], [], []

    if ids:
        collection.add(ids=ids, documents=documents, metadatas=metadatas)

    print(f"完成! {file_count} 文件, {collection.count()} 条向量")


def search(query: str, top_k: int = 10):
    """快速搜索测试"""
    from qdrant_client import QdrantClient
    from sentence_transformers import SentenceTransformer

    client = QdrantClient(url=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    model = SentenceTransformer("BAAI/bge-m3")
    vector = model.encode(query).tolist()

    coll_name = os.environ.get("QDRANT_COLLECTION", "codebase")
    results = client.search(
        collection_name=coll_name,
        query_vector=vector,
        limit=top_k,
    )
    for i, hit in enumerate(results):
        p = hit.payload
        print(f"#{i+1} [{p['repo']}] {p['path']}:L{p['start_line']} "
              f"(score: {hit.score:.3f})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="代码向量化索引工具")
    parser.add_argument("--backend", choices=["qdrant", "chroma"], default="qdrant")
    parser.add_argument("--search", type=str, help="搜索测试")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()

    if args.search:
        search(args.search, args.top_k)
    elif args.backend == "chroma":
        build_chroma_index()
    else:
        build_qdrant_index()
