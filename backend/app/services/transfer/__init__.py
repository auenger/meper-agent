"""资源导入导出（Transfer）服务层。

子模块::

    package.py   afpkg zip 打包 / 解包 / 安全校验 / manifest 读写
    exporter.py  依赖闭包收集 + 各资源导出器
    importer.py  拓扑导入、ID 重映射、冲突/复用判定、落库落盘
    rewiring.py  引用字段重写表 + MCP 工具引用重绑
    report.py    ImportReport 收集器

设计文档：``docs/resource-transfer-plan.md``。
"""
