from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any


TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_sourcebot",
        "description": "精确关键词/正则代码搜索，适合搜索函数名、类名、字符串、import/require 语句。无需指定仓库名。",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索词或正则表达式"}},
            "required": ["query"],
        },
    },
    {
        "name": "search_qdrant",
        "description": "语义向量搜索，适合自然语言描述的功能定位、概念搜索。",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "自然语言搜索描述"}},
            "required": ["query"],
        },
    },
    {
        "name": "search_ast_structure",
        "description": "AST 结构索引搜索，适合按符号名查定义位置、调用者和被调用者关系。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "符号名或结构查询"},
                "repo": {"type": "string", "description": "可选，限定仓库名"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_graph_relations",
        "description": "Neo4j 图遍历搜索，适合查调用链、影响范围和间接依赖关系。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "符号名或图查询"},
                "repo": {"type": "string", "description": "可选，限定仓库名"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_manifest",
        "description": "读取仓库依赖清单（package.json / pyproject.toml 等），适合确认包依赖和版本声明。",
        "input_schema": {
            "type": "object",
            "properties": {"repo": {"type": "string", "description": "仓库名"}},
            "required": ["repo"],
        },
    },
    {
        "name": "local_tool_grep",
        "description": "仓库内正则 grep，适合定位某个符号/字符串在目标仓库哪些文件中出现。",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "目标仓库名"},
                "pattern": {"type": "string", "description": "正则表达式"},
                "include": {"type": "array", "items": {"type": "string"}, "description": "文件白名单 glob"},
                "exclude": {"type": "array", "items": {"type": "string"}, "description": "文件黑名单 glob"},
                "context_lines": {"type": "integer", "description": "上下文行数"},
            },
            "required": ["repo", "pattern"],
        },
    },
    {
        "name": "local_tool_read",
        "description": "读取仓库内某个文件的内容，可选行范围。",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "目标仓库名"},
                "path": {"type": "string", "description": "仓库内相对文件路径"},
                "start_line": {"type": "integer", "description": "起始行（1-based）"},
                "end_line": {"type": "integer", "description": "结束行（1-based，包含）"},
            },
            "required": ["repo", "path"],
        },
    },
    {
        "name": "local_tool_list",
        "description": "列出仓库内某个目录的文件/子目录列表。",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "目标仓库名"},
                "dir_path": {"type": "string", "description": "仓库内相对目录，空字符串为根目录"},
                "include": {"type": "array", "items": {"type": "string"}, "description": "文件白名单 glob"},
                "exclude": {"type": "array", "items": {"type": "string"}, "description": "文件黑名单 glob"},
            },
            "required": ["repo"],
        },
    },
]


def _resolve_repo(repo: str, evidence_pack: dict[str, Any], repos_root: str) -> str | None:
    repo_roots_map = evidence_pack.get("repo_roots", {})
    if repo in repo_roots_map and os.path.isdir(repo_roots_map[repo]):
        return repo

    short = repo.rsplit("/", 1)[-1]
    if short in repo_roots_map and os.path.isdir(repo_roots_map[short]):
        return short
    if os.path.isdir(os.path.join(repos_root, short)):
        return short

    for key in repo_roots_map:
        if (repo.endswith("/" + key) or key.endswith("/" + repo)) and os.path.isdir(repo_roots_map[key]):
            return key
    return None


def _evidence_as_results(evidence_pack: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"repo": e.get("repo", ""), "path": e.get("path", ""), "content": e.get("content", "")}
        for e in evidence_pack.get("evidence", [])
    ]


def _missing_required_args(tool_name: str, args: dict[str, Any]) -> str | None:
    schema = next((tool for tool in TOOLS if tool.get("name") == tool_name), None)
    required = schema.get("input_schema", {}).get("required", []) if schema else []
    missing = [key for key in required if args.get(key) in (None, "")]
    if not missing:
        return None
    return f"(参数缺失: {tool_name} 需要 {', '.join(missing)})"


def dispatch_tool(
    name: str,
    args: dict[str, Any],
    *,
    evidence_pack: dict[str, Any],
    repos_root: str,
    search_sourcebot: Callable[[str, int], list[dict[str, Any]]] | None = None,
    search_qdrant: Callable[[str, int], list[dict[str, Any]]] | None = None,
    search_ast_structure: Callable[[str, list[dict[str, Any]], int], list[str]] | None = None,
    search_graph_relations: Callable[[str, list[dict[str, Any]], int], list[str]] | None = None,
    read_manifest: Callable[..., list[Any]] | None = None,
    local_tool_grep: Callable[..., list[Any]] | None = None,
    local_tool_read: Callable[..., Any] | None = None,
    local_tool_list: Callable[..., list[Any]] | None = None,
) -> str:
    if read_manifest is None or local_tool_grep is None or local_tool_read is None or local_tool_list is None:
        from .precision import read_manifest as default_read_manifest
        from .precision import local_tool_grep as default_local_tool_grep
        from .precision import local_tool_read as default_local_tool_read
        from .precision import local_tool_list as default_local_tool_list

        read_manifest = read_manifest or default_read_manifest
        local_tool_grep = local_tool_grep or default_local_tool_grep
        local_tool_read = local_tool_read or default_local_tool_read
        local_tool_list = local_tool_list or default_local_tool_list

    missing_args = _missing_required_args(name, args)
    if missing_args:
        return missing_args

    if name == "search_sourcebot":
        if not search_sourcebot:
            return "(search_sourcebot 未配置)"
        return json.dumps(search_sourcebot(args["query"], 5), ensure_ascii=False)[:3000]

    if name == "search_qdrant":
        if not search_qdrant:
            return "(search_qdrant 未配置)"
        return json.dumps(search_qdrant(args["query"], 5), ensure_ascii=False)[:3000]

    if name == "search_ast_structure":
        if not search_ast_structure:
            return "(search_ast_structure 未配置)"
        repo = args.get("repo")
        ctx = _evidence_as_results(evidence_pack)
        if repo:
            ctx = [r for r in ctx if r["repo"] == repo] or ctx
        facts = search_ast_structure(args["query"], ctx, 8)
        return "\n".join(facts) if facts else "(无命中)"

    if name == "search_graph_relations":
        if not search_graph_relations:
            return "(search_graph_relations 未配置)"
        repo = args.get("repo")
        ctx = _evidence_as_results(evidence_pack)
        if repo:
            ctx = [r for r in ctx if r["repo"] == repo] or ctx
        facts = search_graph_relations(args["query"], ctx, 8)
        return "\n".join(facts) if facts else "(无命中)"

    if name == "read_manifest":
        resolved = _resolve_repo(args["repo"], evidence_pack, repos_root)
        hits = read_manifest(repos_root, resolved) if resolved else []
        if not hits:
            return "(未找到 manifest)"
        return json.dumps([{"path": h.path, "content": h.content[:500]} for h in hits], ensure_ascii=False)

    if name == "local_tool_grep":
        resolved = _resolve_repo(args["repo"], evidence_pack, repos_root)
        if not resolved:
            return f"(仓库 '{args['repo']}' 不在索引中)"
        kwargs: dict[str, Any] = {"pattern": args["pattern"], "max_matches": 20}
        if args.get("include"):
            kwargs["include"] = args["include"]
        if args.get("exclude"):
            kwargs["exclude"] = args["exclude"]
        if args.get("context_lines") is not None:
            kwargs["context_lines"] = args["context_lines"]
        hits = local_tool_grep(repos_root, resolved, **kwargs)
        if not hits:
            return "(无匹配)"
        return json.dumps(
            [{"path": h.path, "line_range": h.line_range, "content": h.content[:200]} for h in hits[:10]],
            ensure_ascii=False,
        )

    if name == "local_tool_read":
        resolved = _resolve_repo(args["repo"], evidence_pack, repos_root)
        if not resolved:
            return f"(仓库 '{args['repo']}' 不在索引中)"
        hit = local_tool_read(
            repos_root,
            resolved,
            path=args["path"],
            start_line=args.get("start_line"),
            end_line=args.get("end_line"),
            max_lines=200,
        )
        return hit.content[:3000] if hit and hit.content else "(文件不存在或为空)"

    if name == "local_tool_list":
        resolved = _resolve_repo(args["repo"], evidence_pack, repos_root)
        if not resolved:
            return f"(仓库 '{args['repo']}' 不在索引中)"
        kwargs = {"dir_path": args.get("dir_path", ""), "max_entries": 100}
        if args.get("include"):
            kwargs["include"] = args["include"]
        if args.get("exclude"):
            kwargs["exclude"] = args["exclude"]
        entries = local_tool_list(repos_root, resolved, **kwargs)
        if not entries:
            return "(目录为空)"
        return json.dumps(
            [
                {"path": e.path, "type": e.metadata.get("type", "?"), "size": e.metadata.get("size", 0)}
                for e in entries[:30]
            ],
            ensure_ascii=False,
        )

    return f"未知工具: {name}"
