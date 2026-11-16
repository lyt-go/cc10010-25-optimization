"""元数据影响范围重算业务规则。

数据链路：
    过期批次（expire_batch）
        + 依赖字段数据（dep_field，承接依赖关系与 SQL 模板的字段别名）
        + 元数据版本（metastore_version，含确认状态与表结构）
        -> 影响对象数据清单（impact_object）

关键口径：
- 同一张表的所有影响对象只认一个版本来源：优先取「已确认」的最大版本号；
  没有已确认版本时取最大版本号并整体标记为待复核（新版本待确认）。
- 影响对象承接原有依赖关系 / SQL 模板编号与字段别名，差异按字段逐条标出。
- 同一过期批次重复刷新只保留一组影响对象；重算按表落检查点，中断后从
  「最后完整元数据表」之后继续，已完成表不重复计算。
- 新版本重新确认后，相关批次的检查点与旧清单整体作废，按新字段结构重算。
- 元数据存储、依赖解析两处都通过 list_impacts 读同一张 impact_object 表。
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Any

from app.store import store

VERSION_MODULE = "metastore_version"
BATCH_MODULE = "expire_batch"
DEP_FIELD_MODULE = "dep_field"
IMPACT_MODULE = "impact_object"

# 影响原因口径，概览看板按此顺序排列。
REASON_FIELD_REMOVED = "字段删除"
REASON_FIELD_RENAMED = "字段改名"
REASON_SCHEMA_MISMATCH = "结构不一致"
REASON_TEMPLATE_ALIAS = "模板别名失效"
REASON_VERSION_UNCONFIRMED = "新版本待确认"
REASON_ORDER = [
    REASON_FIELD_REMOVED,
    REASON_FIELD_RENAMED,
    REASON_SCHEMA_MISMATCH,
    REASON_TEMPLATE_ALIAS,
    REASON_VERSION_UNCONFIRMED,
]

BATCH_PENDING = "待重算"
BATCH_RUNNING = "重算中"
BATCH_DONE = "已完成"

REVIEW_PENDING = "待复核"
REVIEW_DONE = "已复核"

CONFIRMED = "已确认"
EXPIRED = "已过期"


class ImpactRecalcError(RuntimeError):
    """重算过程中的业务错误（如断点模拟、批次不存在）。"""


class ImpactService:
    def __init__(self) -> None:
        # 内存实现用进程锁保证同一批次不会被并发重算出两组清单。
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # 元数据版本
    # ------------------------------------------------------------------
    def list_versions(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        table_code: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(VERSION_MODULE)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("表编号", "")) or keyword in str(row.get("表名", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("确认状态") == status]
        if table_code:
            rows = [row for row in rows if row.get("表编号") == table_code]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_version(self, version_id: int) -> dict[str, Any] | None:
        return store.find(VERSION_MODULE, version_id)

    def get_last_version(self, table_code: str) -> dict[str, Any] | None:
        """返回一张表版本号最大的元数据记录（详情页展示最后一版）。"""
        candidates = [
            row for row in store.rows(VERSION_MODULE) if row.get("表编号") == table_code
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda row: int(row.get("version_no", 0)))

    def _versions_of(self, table_code: str) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(VERSION_MODULE) if row.get("表编号") == table_code
        ]

    def _resolve_source_version(self, table_code: str) -> tuple[dict[str, Any], bool]:
        """选版本来源：已确认的最大版本号优先；否则取最大版本号并标记未确认。

        返回 (版本记录, 是否已确认)。旧版本与新确认版本冲突时，统一采用该
        版本，保证同表影响对象、确认状态、表结构来自同一版本来源。
        """
        versions = self._versions_of(table_code)
        confirmed = [row for row in versions if row.get("确认状态") == CONFIRMED]
        if confirmed:
            return max(confirmed, key=lambda row: int(row.get("version_no", 0))), True
        return max(versions, key=lambda row: int(row.get("version_no", 0))), False

    def confirm_version(self, version_id: int) -> tuple[dict[str, Any] | None, str]:
        """确认一个新版本，并让引用该表的过期批次按新结构整体重算。"""
        with self._lock:
            version = store.find(VERSION_MODULE, version_id)
            if version is None:
                return None, f"元数据版本 {version_id} 不存在"
            if version.get("确认状态") == CONFIRMED:
                return version, "该版本已是确认状态，无需重复确认"
            version["确认状态"] = CONFIRMED
            version["确认时间"] = date.today().isoformat()
            version["最近更新时间"] = version["确认时间"]
            # 同表更早的已确认版本让位为已过期，保留历史版本记录。
            for older in self._versions_of(str(version["表编号"])):
                if older is not version and older.get("确认状态") == CONFIRMED \
                        and int(older.get("version_no", 0)) < int(version.get("version_no", 0)):
                    older["确认状态"] = EXPIRED
            # 所有涉及该表的批次作废旧清单，按新字段结构重新计算。
            table_code = str(version["表编号"])
            for batch in store.rows(BATCH_MODULE):
                if table_code in batch.get("涉及表编号", []):
                    self._invalidate_batch(batch)
                    self.recompute_batch(int(batch["id"]))
            return version, "元数据版本已确认，相关过期批次已按新字段结构重新计算"

    # ------------------------------------------------------------------
    # 过期批次
    # ------------------------------------------------------------------
    def list_batches(self) -> list[dict[str, Any]]:
        return store.rows(BATCH_MODULE)

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        return store.find(BATCH_MODULE, batch_id)

    def _invalidate_batch(self, batch: dict[str, Any]) -> None:
        """重置批次检查点，强制下一次重算从头开始。

        旧影响对象暂不删除：重算按表推进时会用新结果逐表原子替换，
        这样上一版的人工复核结论可以承接到重新计算后的同口径对象上。
        """
        batch["批次状态"] = BATCH_PENDING
        batch["重算进度"] = 0
        batch["已完成表"] = []
        batch["最后完整表"] = ""
        batch["影响对象数"] = 0
        batch["最近重算时间"] = ""

    def recompute_batch(
        self,
        batch_id: int,
        *,
        fail_after_tables: int | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """重算一个过期批次的影响对象清单。

        - 已完成批次再次调用直接返回现有清单（幂等，只保留一组影响对象）。
        - 重算中 / 中断的批次从「最后完整表」之后续算，已完成表跳过。
        - fail_after_tables 仅供演练断点：本次新处理 N 张表后抛错，检查点已落盘。
        """
        with self._lock:
            batch = store.find(BATCH_MODULE, batch_id)
            if batch is None:
                return None, f"过期批次 {batch_id} 不存在"
            if batch.get("批次状态") == BATCH_DONE:
                return batch, "该批次已完成重算，沿用现有影响对象清单"

            batch["批次状态"] = BATCH_RUNNING
            tables = sorted(batch.get("涉及表编号", []))
            done: list[str] = list(batch.get("已完成表", []))
            processed_this_call = 0

            for table_code in tables:
                if table_code in done:
                    # 断点续算：最后完整表之前的结果已落盘，不重复计算。
                    continue
                new_rows = self._compute_table_impacts(batch, table_code)
                if fail_after_tables is not None and processed_this_call >= fail_after_tables:
                    # 演练断点：结果不落盘、检查点不前进，恢复后从本表重新计算。
                    raise ImpactRecalcError(
                        f"批次 {batch['批次编号']} 在表 {table_code} 处理前模拟中断，"
                        "检查点已保留，可再次发起重算从该表续算"
                    )
                # 按表原子替换：先剔除该表旧结果再写入新结果，保证同批次同表只有一组清单。
                # 先剔除再取 next_id，避免新行复用旧表已释放的 id 而与其他表主键冲突。
                kept_rows = [
                    row for row in store.rows(IMPACT_MODULE)
                    if not (
                        int(row.get("批次id", 0)) == int(batch["id"])
                        and row.get("表编号") == table_code
                    )
                ]
                for row in new_rows:
                    row["id"] = store.next_id(IMPACT_MODULE)
                store.replace_rows(IMPACT_MODULE, kept_rows + new_rows)
                done.append(table_code)
                batch["已完成表"] = done
                batch["最后完整表"] = table_code
                batch["重算进度"] = round(len(done) / len(tables), 4) if tables else 1
                processed_this_call += 1

            batch["批次状态"] = BATCH_DONE
            batch["重算进度"] = 1
            batch["影响对象数"] = sum(
                1 for row in store.rows(IMPACT_MODULE) if int(row.get("批次id", 0)) == batch_id
            )
            batch["最近重算时间"] = date.today().isoformat()
            return batch, f"批次 {batch['批次编号']} 重算完成，生成 {batch['影响对象数']} 个影响对象"

    # ------------------------------------------------------------------
    # 单表影响计算
    # ------------------------------------------------------------------
    def _compute_table_impacts(
        self,
        batch: dict[str, Any],
        table_code: str,
    ) -> list[dict[str, Any]]:
        source, confirmed = self._resolve_source_version(table_code)
        new_fields = {
            str(item.get("field", "")): item for item in source.get("字段清单", [])
        }
        dep_fields = [
            row for row in store.rows(DEP_FIELD_MODULE)
            if row.get("源表编号") == table_code
        ]

        # key: (来源模块, 来源对象编号, 影响原因) -> 影响对象行
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}

        def build_row(reason: str, dep: dict[str, Any], issue: dict[str, Any]) -> None:
            key = (str(dep.get("来源模块", "")), str(dep.get("来源对象编号", "")), reason)
            row = grouped.get(key)
            if row is None:
                row = {
                    "id": 0,  # 落盘前由重算主流程统一分配，避免替换时主键冲突
                    "批次id": int(batch["id"]),
                    "批次编号": batch["批次编号"],
                    "表编号": table_code,
                    "表名": dep.get("源表名", ""),
                    "来源模块": dep.get("来源模块", ""),
                    "来源对象编号": dep.get("来源对象编号", ""),
                    "关系编号": dep.get("关系编号", ""),
                    "SQL模板编号": dep.get("SQL模板编号", ""),
                    "目标对象": dep.get("目标对象", ""),
                    "影响原因": reason,
                    "过期字段列表": [],
                    # 同一表的所有影响对象共用同一版本来源。
                    "版本来源id": int(source.get("id", 0)),
                    "版本来源号": int(source.get("version_no", 0)),
                    "元数据确认状态": source.get("确认状态", ""),
                    "结构一致性": "一致" if confirmed else "待确认",
                    "复核状态": REVIEW_PENDING,
                    "复核备注": "",
                    "更新时间": date.today().isoformat(),
                }
                grouped[key] = row
            row["过期字段列表"].append(issue)

        for dep in dep_fields:
            field_name = str(dep.get("引用字段", ""))
            alias = str(dep.get("字段别名", ""))
            old_type = str(dep.get("字段类型", ""))
            new_meta = new_fields.get(field_name)

            if not confirmed:
                # 新版本尚未确认：无法判定最终结构，全部引用挂起待复核。
                build_row(
                    REASON_VERSION_UNCONFIRMED,
                    dep,
                    {
                        "field": field_name,
                        "旧别名": alias,
                        "旧类型": old_type,
                        "新别名": "",
                        "新类型": "",
                        "问题说明": "最新元数据版本尚未确认，引用是否有效待复核",
                    },
                )
                continue

            if new_meta is None:
                build_row(
                    REASON_FIELD_REMOVED,
                    dep,
                    {
                        "field": field_name,
                        "旧别名": alias,
                        "旧类型": old_type,
                        "新别名": "",
                        "新类型": "",
                        "问题说明": "新元数据版本中该字段已删除",
                    },
                )
                continue

            new_alias = str(new_meta.get("alias", ""))
            new_type = str(new_meta.get("data_type", ""))
            if new_type != old_type:
                build_row(
                    REASON_SCHEMA_MISMATCH,
                    dep,
                    {
                        "field": field_name,
                        "旧别名": alias,
                        "旧类型": old_type,
                        "新别名": new_alias,
                        "新类型": new_type,
                        "问题说明": f"字段类型由 {old_type} 变更为 {new_type}",
                    },
                )
            if new_alias != alias:
                # SQL 模板来源的别名失效单独成类，依赖关系来源记为字段改名。
                reason = (
                    REASON_TEMPLATE_ALIAS
                    if dep.get("来源模块") == "sqltpl"
                    else REASON_FIELD_RENAMED
                )
                build_row(
                    reason,
                    dep,
                    {
                        "field": field_name,
                        "旧别名": alias,
                        "旧类型": old_type,
                        "新别名": new_alias,
                        "新类型": new_type,
                        "问题说明": f"字段别名由「{alias}」变更为「{new_alias}」",
                    },
                )

        rows = list(grouped.values())
        # 同一对象被重算时尽量承接上一版的复核结论，避免重复人工复核。
        previous = {
            self._row_key(row): row
            for row in store.rows(IMPACT_MODULE)
            if int(row.get("批次id", 0)) == int(batch["id"]) and row.get("表编号") == table_code
        }
        for row in rows:
            old = previous.get(self._row_key(row))
            if old and old.get("复核状态") == REVIEW_DONE:
                row["复核状态"] = REVIEW_DONE
                row["复核备注"] = old.get("复核备注", "")
        return rows

    @staticmethod
    def _row_key(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row.get("批次id"),
            row.get("表编号"),
            row.get("来源模块"),
            row.get("来源对象编号"),
            row.get("影响原因"),
        )

    # ------------------------------------------------------------------
    # 影响对象清单（元数据存储 / 依赖解析共用的取数口径）
    # ------------------------------------------------------------------
    def list_impacts(
        self,
        *,
        batch_id: int | None = None,
        table_code: str | None = None,
        table_name: str | None = None,
        reason: str | None = None,
        review_status: str | None = None,
        source_module: str | None = None,
        relation_no: str | None = None,
        sql_tpl_no: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(IMPACT_MODULE)
        if batch_id is not None:
            rows = [row for row in rows if int(row.get("批次id", 0)) == batch_id]
        if table_code:
            rows = [row for row in rows if row.get("表编号") == table_code]
        if table_name:
            rows = [row for row in rows if table_name in str(row.get("表名", ""))]
        if reason:
            rows = [row for row in rows if row.get("影响原因") == reason]
        if review_status:
            rows = [row for row in rows if row.get("复核状态") == review_status]
        if source_module:
            rows = [row for row in rows if row.get("来源模块") == source_module]
        if relation_no:
            rows = [row for row in rows if row.get("关系编号") == relation_no]
        if sql_tpl_no:
            rows = [row for row in rows if row.get("SQL模板编号") == sql_tpl_no]
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("来源对象编号", ""))
                or keyword in str(row.get("目标对象", ""))
                or keyword in str(row.get("关系编号", ""))
                or keyword in str(row.get("SQL模板编号", ""))
            ]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def review_impact(self, impact_id: int, remark: str | None = None) -> tuple[dict[str, Any] | None, str]:
        row = store.find(IMPACT_MODULE, impact_id)
        if row is None:
            return None, f"影响对象 {impact_id} 不存在或已随批次重算刷新"
        row["复核状态"] = REVIEW_DONE
        if remark:
            row["复核备注"] = remark
        return row, "影响对象已标记复核完成"

    def impact_board(self) -> dict[str, Any]:
        """概览看板：按影响原因排列，并汇总批次与复核进度。"""
        rows = store.rows(IMPACT_MODULE)
        reason_groups = [
            {
                "影响原因": reason,
                "影响对象数": sum(1 for row in rows if row.get("影响原因") == reason),
                "待复核数": sum(
                    1 for row in rows
                    if row.get("影响原因") == reason and row.get("复核状态") == REVIEW_PENDING
                ),
            }
            for reason in REASON_ORDER
        ]
        reason_groups = [group for group in reason_groups if group["影响对象数"]]
        batches = [
            {
                "批次编号": batch.get("批次编号"),
                "批次状态": batch.get("批次状态"),
                "重算进度": batch.get("重算进度"),
                "影响对象数": batch.get("影响对象数"),
                "最后完整表": batch.get("最后完整表"),
            }
            for batch in store.rows(BATCH_MODULE)
        ]
        return {
            "影响对象总数": len(rows),
            "待复核总数": sum(1 for row in rows if row.get("复核状态") == REVIEW_PENDING),
            "按影响原因": reason_groups,
            "批次进度": batches,
        }

    def list_dependency_reviews(
        self,
        *,
        relation_no: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """依赖解析侧取数：读同一份影响对象清单并标出待复核关系。

        每条记录都带版本来源号与过期字段，可直接定位过期字段和最后一版元数据。
        """
        rows, total = self.list_impacts(
            source_module="dependency",
            relation_no=relation_no,
            page=page,
            size=size,
        )
        for row in rows:
            row["待复核标志"] = row.get("复核状态") == REVIEW_PENDING
            version = self.get_version(int(row.get("版本来源id", 0)))
            row["最后一版元数据"] = version
        return rows, total


impact_service = ImpactService()
