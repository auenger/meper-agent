"""一次性修复脚本：刷新存量 vector KB 的 file_count / total_size。

背景：
    ``KnowledgeBaseService.recompute_vector_stats`` 只在上传/删除文档时
    被调用（见 kb_service.py / knowledge_bases.py）。在该逻辑上线之前创建
    的 vector KB，其 file_count/total_size 恒为 0，前端列表卡片会显示
    「0 文件 / 0 B」。本脚本扫描所有 vector KB 并用 knowledge_documents
    集合的真实数据补齐。

用法：
    cd backend
    source .venv/bin/activate
    # 干跑（只打印将变更的内容，不写库）
    python scripts/repair_vector_kb_stats.py --dry-run
    # 实跑
    python scripts/repair_vector_kb_stats.py
"""
from __future__ import annotations

import argparse
import asyncio
import sys

# 确保能 import app.*
sys.path.insert(0, ".")


async def repair(*, dry_run: bool) -> None:
    from app.services.kb_service import KnowledgeBaseService
    from app.services.knowledge_document_service import (
        KnowledgeDocumentService,
    )

    print(f"模式: {'干跑（不写库）' if dry_run else '实跑'}")
    print()

    # 仅处理 vector KB
    col = KnowledgeBaseService._collection()
    cursor = col.find({"type": "vector"})
    vector_kbs = await cursor.to_list(length=10000)
    print(f"找到 {len(vector_kbs)} 个 vector KB")

    updated = 0
    unchanged = 0
    for kb in vector_kbs:
        kb_id = kb["_id"]
        old_count = kb.get("file_count", 0)
        old_size = kb.get("total_size", 0)
        new_count, new_size = await KnowledgeDocumentService.compute_kb_stats(kb_id)

        if new_count == old_count and new_size == old_size:
            unchanged += 1
            print(f"  · {kb_id} [{kb.get('name', '')}] 已是最新 ({new_count} 文件 / {new_size} B)")
            continue

        print(
            f"  ✎ {kb_id} [{kb.get('name', '')}] "
            f"{old_count}→{new_count} 文件, {old_size}→{new_size} B"
        )
        if not dry_run:
            await KnowledgeBaseService.recompute_vector_stats(kb_id)
        updated += 1

    print()
    print("=" * 40)
    if dry_run:
        print(f"干跑完成：{updated} 个 KB 待更新，{unchanged} 个已最新。去掉 --dry-run 实跑。")
    else:
        print(f"修复完成：{updated} 个 KB 已更新，{unchanged} 个已最新。")


def main() -> None:
    parser = argparse.ArgumentParser(description="刷新存量 vector KB 的 file_count/total_size")
    parser.add_argument("--dry-run", action="store_true", help="只打印变更，不写库")
    args = parser.parse_args()
    asyncio.run(repair(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
