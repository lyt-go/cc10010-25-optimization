"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 各模块的高水位主键：即使旧行被整表替换，新主键也只增不复用，
        # 避免跨批次/跨表重算时出现重复 id。
        self._id_sequences: dict[str, int] = {
            name: max((int(row.get("id", 0)) for row in rows), default=0)
            for name, rows in self._tables.items()
        }

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def next_id(self, module: str) -> int:
        """基于高水位序列分配模块内自增主键，替换/删除旧行后仍不复用 id。"""
        current = max(
            self._id_sequences.get(module, 0),
            max((int(row.get("id", 0)) for row in self.rows(module)), default=0),
        )
        current += 1
        self._id_sequences[module] = current
        return current

    def replace_rows(self, module: str, rows: list[dict[str, Any]]) -> None:
        """整表替换：影响对象重算时用同一批新结果覆盖旧清单。"""
        self._tables[module] = rows
        self._id_sequences[module] = max(
            self._id_sequences.get(module, 0),
            max((int(row.get("id", 0)) for row in rows), default=0),
        )

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        # 影响范围重算链路的内部数据表不进运营概览，保持原有业务模块口径。
        internal_modules = {"metastore_version", "expire_batch", "dep_field", "impact_object"}
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            if name in internal_modules:
                continue
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
