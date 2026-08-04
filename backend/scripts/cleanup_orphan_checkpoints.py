"""一次性清理脚本：删除 LangGraph checkpointer 的孤儿数据。

背景：
    历史 bug:删除会话 / 删除 task 时未清理 checkpointer,导致
    ``checkpoints`` 和 ``checkpoint_writes`` 集合中残留大量孤儿文档。
    本脚本扫描这两个集合,删除其 ``thread_id`` 已无对应业务实体的文档。

    thread_id 的两种形式(与写入逻辑一致,见):
      - 会话: ``thread_id == session_id``(精确相等,无前缀)
        参考 app/engine/harness_integration/execution.py
      - 任务节点: ``thread_id == f"{task_id}_{node_id}"``
        参考 app/engine/workflow/node_executor.py

    判活规则:某个 thread_id 只要满足以下任一条件即视为「仍存活」,不删:
      - 它恰好等于某个 sessions._id(会话型)
      - 它以某个 tasks._id + "_" 为前缀(任务型)

    其余即为孤儿,删除。

用法:
    cd backend
    source .venv/bin/activate
    # 干跑(只打印将删除的内容,不写库)
    python scripts/cleanup_orphan_checkpoints.py --dry-run
    # 实跑
    python scripts/cleanup_orphan_checkpoints.py
"""
from __future__ import annotations

import argparse
import asyncio
import sys

# 确保能 import app.*
sys.path.insert(0, ".")

# 应用从未覆盖 MongoDBSaver 的默认集合名(见 langgraph mongodb saver 源码),
# 故可直接用这两个固定集合名,无需构造 saver 单例。
CHECKPOINT_COLLECTIONS = ("checkpoints", "checkpoint_writes")


async def _load_alive_thread_prefixes(db) -> set[str]:
    """加载活跃 task_id 集合(用于前缀匹配)与活跃 session_id 集合(精确匹配)。"""
    # task_id 形如 "task_xxx";任务型 thread_id = f"{task_id}_{node_id}"
    task_ids = {
        doc["_id"] async for doc in db["tasks"].find({}, {"_id": 1})
    }
    # 预生成前缀集合 f"{task_id}_",便于 O(1) 判断「是否以某个 task_id_ 开头」。
    # 注意:task_id 本身含 "_"(如 task_xxx),但前缀匹配要求 thread_id 去掉 node_id
    # 部分后剩下的正好是 task_id + "_",所以直接用完整 task_id 拼接 "_"。
    task_prefixes = {f"{tid}_" for tid in task_ids}
    return task_prefixes


async def _load_alive_session_ids(db) -> set[str]:
    """加载活跃 session_id 集合(精确匹配)。"""
    return {
        doc["_id"] async for doc in db["sessions"].find({}, {"_id": 1})
    }


def _is_alive(
    thread_id: str,
    alive_sessions: set[str],
    task_prefixes: set[str],
) -> bool:
    """判断 thread_id 是否仍对应活跃业务实体。

    会话型: thread_id 精确等于某个 session_id。
    任务型: thread_id 以某个 f"{task_id}_" 前缀开头。

    task_prefixes 已是完整前缀(含末尾下划线),但 thread_id = f"{task_id}_{node_id}"
    可能含多个下划线,因此不能简单 startswith 某个完整前缀——需要检查
    thread_id 是否以 task_prefixes 中任一项开头。由于 task_id 集合通常不大,
    这里直接遍历;若量级极大可改用按下划线切分 + 最长前缀匹配。
    """
    if thread_id in alive_sessions:
        return True
    return any(thread_id.startswith(p) for p in task_prefixes)


async def cleanup(*, dry_run: bool) -> None:
    from app.core.config import settings
    from app.db.mongodb import get_database

    db = get_database()
    print(f"数据库: {settings.MONGODB_DB_NAME}")
    print(f"模式: {'干跑(不写库)' if dry_run else '实跑'}")
    print()

    # 1. 加载活跃实体
    alive_sessions = await _load_alive_session_ids(db)
    task_prefixes = await _load_alive_thread_prefixes(db)
    print(
        f"活跃实体: {len(alive_sessions)} 个会话, "
        f"{len(task_prefixes)} 个任务(前缀)"
    )
    print()

    # 2. 扫描每个 checkpoint 集合,收集孤儿 thread_id
    total_orphan_docs = 0
    total_orphan_threads = 0
    for col_name in CHECKPOINT_COLLECTIONS:
        col = db[col_name]
        # distinct 拿到该集合所有 thread_id(去重),远小于全表扫描
        thread_ids = await col.distinct("thread_id")
        orphan_threads = [
            tid for tid in thread_ids
            if not _is_alive(tid, alive_sessions, task_prefixes)
        ]

        if not orphan_threads:
            print(f"· {col_name}: 无孤儿")
            continue

        # 统计这些孤儿 thread 对应的文档数
        orphan_doc_count = await col.count_documents(
            {"thread_id": {"$in": orphan_threads}}
        )
        total_orphan_docs += orphan_doc_count
        total_orphan_threads += len(orphan_threads)

        preview = orphan_threads[:10]
        suffix = f" ...(共 {len(orphan_threads)} 个)" if len(orphan_threads) > 10 else ""
        print(
            f"✎ {col_name}: {len(orphan_threads)} 个孤儿 thread, "
            f"{orphan_doc_count} 个文档待删"
        )
        for tid in preview:
            print(f"    - {tid}")
        if suffix:
            print(f"    {suffix}")

        if not dry_run:
            result = await col.delete_many(
                {"thread_id": {"$in": orphan_threads}}
            )
            print(f"  ✅ 已删除 {result.deleted_count} 个文档")

    print()
    print("=" * 40)
    if dry_run:
        print(
            f"干跑完成:共 {total_orphan_threads} 个孤儿 thread, "
            f"{total_orphan_docs} 个文档待删。去掉 --dry-run 实跑。"
        )
    else:
        print(
            f"清理完成:共删除 {total_orphan_threads} 个孤儿 thread, "
            f"{total_orphan_docs} 个文档。"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="清理 checkpointer 孤儿数据(checkpoints / checkpoint_writes)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印将删除的内容,不写库"
    )
    args = parser.parse_args()
    asyncio.run(cleanup(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
