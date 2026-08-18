"""parse_file —— 内置文件解析工具，按需提取 Office/PDF 文件内容供 agent 分析。

对话附件渲染层（file_rendering.py）只认文本 MIME，Office/PDF 上传后 agent 只能看到
占位符。本工具补上这个空档：按 file_id（对话 <attachments> 块暴露的 id）或
workspace 相对路径（input/ output/，覆盖 workflow file_paths 场景与 agent 自产文件）
读取文件字节，复用 KB 向量链路的纯函数解析器（app/engine/kb/vector/parser），
提取文本拼成 markdown 返回 —— LLM 可读，前端 tool_result 区也可直接 Markdown 渲染。
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import BaseTool, tool

# KB parser 支持的文件类型（app/engine/kb/vector/parser/base.py parse 的分发集）
SUPPORTED_TYPES = {"docx", "xlsx", "pptx", "pdf", "csv", "md", "txt", "html", "htm"}

# 旧版 Office 二进制格式 —— python-docx/openpyxl/python-pptx 均无法解析
LEGACY_TYPES = {"doc", "xls", "ppt"}

# 结果硬上限（对齐 file_rendering._MAX_ATTACHMENT_CHARS，防 max_chars 滥用撑爆上下文）
_HARD_CHAR_CAP = 50_000


def _err(msg: str) -> str:
    """返回友好错误文本（不抛异常，让 agent 能读懂并修正）。"""
    return f"[parse_file] {msg}"


def _file_type_of(name: str) -> str:
    """从文件名提取小写后缀（无点）。"""
    return Path(name).suffix.lower().lstrip(".")


def _resolve_path(path: str, workspace) -> Path | str:
    """把 workspace 相对路径安全解析到 input/ 或 output/ 树内。

    Returns:
        绝对 Path 或错误文本。
    """
    from app.engine.tool.workspace import WorkspaceManager

    resolved = WorkspaceManager.safe_resolve_path(workspace.root, path)
    if resolved is None or not resolved.is_file():
        return _err(f"工作区中不存在文件：{path}")

    allowed_roots = (workspace.input_dir.resolve(), workspace.output_dir.resolve())
    if not any(
        str(resolved).startswith(str(r) + "/") or resolved == r for r in allowed_roots
    ):
        return _err(f"路径越界：{path}（仅允许 input/ 与 output/ 目录）")
    return resolved


def _render_markdown(name: str, file_type: str, result, limit: int) -> str:
    """把 ParseResult 拼成 markdown（section/页码作小标题）。"""
    # xlsx/pptx 的 parser 把 sheet/slide 序号填进 total_pages,按类型特判摘要。
    summary = f"{len(result.blocks)} 个内容块"
    if file_type == "xlsx":
        summary = f"{len(result.blocks)} 个工作表"
    elif file_type == "pptx":
        summary = f"{len(result.blocks)} 张幻灯片"
    elif result.total_pages:
        summary = f"{result.total_pages} 页"

    parts = [f"# {name}（{file_type}，{summary}）"]
    for block in result.blocks:
        if block.section:
            parts.append(f"\n## {block.section}\n")
        elif block.page:
            parts.append(f"\n## 第 {block.page} 部分\n")
        parts.append(block.text)

    text = "\n".join(parts)
    if len(text) > limit:
        text = text[:limit] + f"\n\n[… 内容已截断，原文共 {len(text)} 字符，可提高 max_chars 分段读取 …]"
    return text


@tool
async def parse_file(
    file_id: str = "",
    path: str = "",
    structured: bool = False,
    max_chars: int = 30_000,
) -> str:
    """Parse an uploaded Office/PDF file and return its content as markdown.

    Use when the user attached a document (Word/Excel/PowerPoint/PDF/CSV) and
    asks to analyze, summarize, or extract from it — the attachment preview in
    context only shows metadata for binary files, this tool reads the actual
    content. Get ``file_id`` from the <file> tag's id attribute in the
    <attachments> block; or pass a workspace-relative ``path`` (under input/
    or output/) for files referenced by path only.

    Args:
        file_id: File id from the <attachments> block (starts with "file_").
        path: Workspace-relative path (e.g. "input/report.docx"). Alternative
            to file_id — use whichever the context gives you.
        structured: For docx/html — keep heading structure as sections.
        max_chars: Max characters to return (1000-50000, default 30000).
            (Named max_chars — a plain `config` param collides with langchain's
            internal RunnableConfig plumbing and gets dropped.)
    """
    from app.engine.agent.builtin_tools import _get_workspace

    workspace = _get_workspace()
    if workspace is None:
        return _err("当前无工作区上下文，无法解析文件")

    if not file_id and not path:
        return _err("需要提供 file_id（附件块的 id 属性）或 path（工作区相对路径）之一")

    # ── 取字节与文件名 ─────────────────────────────────────────────
    if file_id:
        from app.services.file_service import FileService
        from app.services.file_storage import LocalFileStorage

        loaded = await FileService(storage=LocalFileStorage()).load_content(file_id)
        if loaded is None:
            return _err(f"文件不存在：{file_id}")
        fref, data = loaded
        if fref.owner_user_id != workspace.user_id:
            return _err(f"文件 {file_id} 不属于当前用户，无权访问")
        name, file_type = fref.name, _file_type_of(fref.name)
    else:
        resolved = _resolve_path(path, workspace)
        if isinstance(resolved, str):
            return resolved
        name, file_type = resolved.name, _file_type_of(resolved.name)
        try:
            data = resolved.read_bytes()
        except OSError as e:
            return _err(f"读取文件失败：{path}（{e}）")

    # ── 类型校验 ───────────────────────────────────────────────────
    if file_type in LEGACY_TYPES:
        new = {"doc": "docx", "xls": "xlsx", "ppt": "pptx"}[file_type]
        return _err(
            f"不支持旧版二进制格式 .{file_type}，请让用户另存为 .{new} 后重新上传"
        )
    if file_type not in SUPPORTED_TYPES:
        return _err(
            f"不支持的文件类型 .{file_type}（支持：{sorted(SUPPORTED_TYPES)}）"
        )

    # ── 解析 ───────────────────────────────────────────────────────
    from app.engine.kb.vector.parser import parse

    try:
        result = parse(data, file_type, structured=structured)
    except Exception as e:  # parser 抛的解析错误统一转友好文本
        return _err(f"解析 {name} 失败：{e}")

    if not result.blocks or not any(b.text.strip() for b in result.blocks):
        return _err(f"{name} 未提取到文本内容（可能是纯图片/空文档）")

    limit = max(1_000, min(int(max_chars), _HARD_CHAR_CAP))
    return _render_markdown(name, file_type, result, limit)


# ---------------------------------------------------------------------------
# Tool list — exported for context.py injection (builtin_config 白名单过滤)
# ---------------------------------------------------------------------------

_PARSE_TOOLS: list[BaseTool] = [parse_file]

# name → tool 实例查找表：/tools/builtin 端点、context 注入、builder prompt 声明
# 三处共用（harness BUILTIN_TOOLS 取不到 app 层工具，需补此表查找）。
PARSE_TOOL_BY_NAME: dict[str, BaseTool] = {t.name: t for t in _PARSE_TOOLS}
