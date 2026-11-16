"""影响范围重算验收测试（仅依赖标准库，可 python tests/test_impact_recalc.py 直接跑）。

覆盖口径：
1. 从过期批次 + 依赖字段重算并生成影响对象清单；
2. 多个元数据版本同时过期、新旧版本冲突时统一取同一版本来源；
3. 影响对象承接原有依赖关系/SQL 模板编号与字段别名；
4. 影响清单、确认状态、表结构一致性同版本来源；
5. 重复刷新同一批次只生成一组影响对象（幂等）；
6. 重算中断后从最后完整元数据表续算；
7. 重新确认后受影响对象按新字段结构重新计算；
8. 元数据存储与依赖解析两处取数对得上同一份清单；
9. 依赖解析清单可定位过期字段与最后一版元数据；
10. 概览看板按影响原因排列。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.seed import SEED_ROWS  # noqa: E402
from app.services.impact import (  # noqa: E402
    BATCH_DONE,
    EXPIRED,
    REVIEW_DONE,
    REVIEW_PENDING,
    ImpactRecalcError,
    ImpactService,
)
from app.store import Store  # noqa: E402


def fresh_service() -> ImpactService:
    """每个用例使用独立 Store，避免内存数据互相污染。"""
    import app.services.impact as impact_module

    service = ImpactService()
    store = Store()
    impact_module.store = store
    return service


def test_recompute_generates_impacts_from_batch_and_dep_fields() -> None:
    svc = fresh_service()
    batch, msg = svc.recompute_batch(1)
    assert batch is not None and batch["批次状态"] == BATCH_DONE
    assert batch["影响对象数"] == 5
    items, total = svc.list_impacts(batch_id=1, page=1, size=100)
    assert total == 5
    # 承接原有依赖关系编号 / SQL 模板编号 / 字段别名
    depe_rows = [r for r in items if r["来源对象编号"] == "DEPE-0001"]
    assert depe_rows and all(r["关系编号"] == "DEPE-0001" for r in depe_rows)
    tpl_rows = [r for r in items if r["来源对象编号"] == "SQLT-0006"]
    assert tpl_rows and all(r["SQL模板编号"] == "SQLT-0006" for r in tpl_rows)
    renamed = next(r for r in items if r["影响原因"] == "字段改名")
    assert renamed["过期字段列表"][0]["旧别名"] == "买家ID"
    print("ok 1. 批次+依赖字段重算并承接原编号与别名")


def test_version_conflict_uses_single_confirmed_source() -> None:
    svc = fresh_service()
    svc.recompute_batch(1)
    items, _ = svc.list_impacts(batch_id=1, table_code="META-1001", page=1, size=100)
    # META-1001 存在 v1 过期版与 v2 已确认版，清单必须全部来自 v2
    assert items, "META-1001 应产生影响对象"
    assert all(r["版本来源号"] == 2 for r in items)
    assert all(r["元数据确认状态"] == "已确认" for r in items)
    assert all(r["结构一致性"] == "一致" for r in items)
    print("ok 2. 版本冲突统一取最晚已确认版本，清单/确认状态/结构同源")


def test_unconfirmed_version_marked_pending() -> None:
    svc = fresh_service()
    svc.recompute_batch(1)
    items, _ = svc.list_impacts(batch_id=1, table_code="META-1002", page=1, size=100)
    # META-1002 的 v2 仍是评审中：取最大版本但整体待复核
    assert items and all(r["影响原因"] == "新版本待确认" for r in items)
    assert all(r["结构一致性"] == "待确认" for r in items)
    assert all(r["元数据确认状态"] == "评审中" for r in items)
    print("ok 3. 无已确认版本时取最大版本并标记待复核")


def test_idempotent_same_batch_single_set() -> None:
    svc = fresh_service()
    svc.recompute_batch(1)
    svc.recompute_batch(1)
    svc.recompute_batch(1)
    items, total = svc.list_impacts(batch_id=1, page=1, size=100)
    keys = [(r["表编号"], r["来源对象编号"], r["影响原因"]) for r in items]
    assert total == 5 and len(keys) == len(set(keys))
    print("ok 4. 重复刷新同一批次只保留一组影响对象")


def test_resume_from_last_complete_table() -> None:
    svc = fresh_service()
    # 批次2 含两张表，第一张表处理前模拟中断
    try:
        svc.recompute_batch(2, fail_after_tables=1)
        raise AssertionError("应当抛出中断异常")
    except ImpactRecalcError:
        pass
    batch = svc.get_batch(2)
    assert batch["批次状态"] == "重算中"
    assert batch["最后完整表"] == "META-1001"
    assert batch["已完成表"] == ["META-1001"]
    first_part, _ = svc.list_impacts(batch_id=2, page=1, size=100)
    assert first_part and all(r["表编号"] == "META-1001" for r in first_part)
    # 续算：从 META-1002 继续，不重复计算已完成表
    batch, _ = svc.recompute_batch(2)
    assert batch["批次状态"] == BATCH_DONE
    assert batch["最后完整表"] == "META-1002"
    items, total = svc.list_impacts(batch_id=2, page=1, size=100)
    assert {r["表编号"] for r in items} == {"META-1001", "META-1002"}
    # 已完成表结果不重复
    assert total == len({(r["表编号"], r["来源对象编号"], r["影响原因"]) for r in items})
    print("ok 5. 中断后从最后完整元数据表续算且不重复")


def test_reconfirm_recalculates_with_new_structure() -> None:
    svc = fresh_service()
    svc.recompute_batch(1)
    svc.recompute_batch(2)
    before, _ = svc.list_impacts(table_code="META-1002", page=1, size=100)
    assert all(r["影响原因"] == "新版本待确认" for r in before)
    # 确认 v2（id=4），相关批次自动按新结构重算
    version, message = svc.confirm_version(4)
    assert version is not None and version["确认状态"] == "已确认"
    assert "重新计算" in message
    after, _ = svc.list_impacts(table_code="META-1002", page=1, size=100)
    reasons = {r["影响原因"] for r in after}
    # stat_date 被删除 -> 字段删除；sum_amt 精度变化 -> 结构不一致；不再有“待确认”
    assert "字段删除" in reasons and "结构不一致" in reasons
    assert "新版本待确认" not in reasons
    assert all(r["版本来源号"] == 2 and r["结构一致性"] == "一致" for r in after)
    removed = next(r for r in after if r["影响原因"] == "字段删除")
    assert removed["过期字段列表"][0]["field"] == "stat_date"
    # 旧的已确认 v1 让位为已过期
    assert svc.get_version(3)["确认状态"] == EXPIRED
    print("ok 6. 重新确认后按新字段结构重算，旧确认版让位")


def test_two_modules_read_same_impact_list() -> None:
    svc = fresh_service()
    svc.recompute_batch(1)
    svc.recompute_batch(2)
    meta_side, meta_total = svc.list_impacts(source_module="dependency", page=1, size=100)
    dep_side, dep_total = svc.list_dependency_reviews(page=1, size=100)
    assert meta_total == dep_total
    assert sorted(r["id"] for r in meta_side) == sorted(r["id"] for r in dep_side)
    # 跨模块清单版本来源一致
    assert {r["版本来源id"] for r in meta_side} == {r["版本来源id"] for r in dep_side}
    print("ok 7. 元数据存储与依赖解析读取同一份影响清单")


def test_dependency_queue_locates_fields_and_last_version() -> None:
    svc = fresh_service()
    svc.recompute_batch(1)
    rows, total = svc.list_dependency_reviews(page=1, size=100)
    assert total >= 1
    for row in rows:
        assert row["待复核标志"] == (row["复核状态"] == REVIEW_PENDING)
        assert row["过期字段列表"], "每条记录必须带得出过期字段"
        last = row["最后一版元数据"]
        assert last is not None and last["id"] == row["版本来源id"]
        assert last["version_no"] >= row["版本来源号"]
    # 复核后标志翻转
    target = rows[0]
    reviewed, _ = svc.review_impact(int(target["id"]), "已处理")
    assert reviewed["复核状态"] == REVIEW_DONE
    rows2, _ = svc.list_dependency_reviews(
        relation_no=str(target["关系编号"]), page=1, size=100
    )
    target2 = next(r for r in rows2 if r["id"] == target["id"])
    assert target2["待复核标志"] is False
    print("ok 8. 依赖解析清单可定位过期字段与最后一版元数据并支持复核")


def test_board_groups_by_reason() -> None:
    svc = fresh_service()
    svc.recompute_batch(1)
    board = svc.impact_board()
    reasons = [g["影响原因"] for g in board["按影响原因"]]
    # 按既定原因顺序排列
    order = ["字段删除", "字段改名", "结构不一致", "模板别名失效", "新版本待确认"]
    assert reasons == [r for r in order if r in reasons]
    assert board["影响对象总数"] == sum(g["影响对象数"] for g in board["按影响原因"])
    assert len(board["批次进度"]) == 2
    print("ok 9. 概览看板按影响原因排列且批次进度齐全")


def test_review_conclusion_carried_over_on_recompute() -> None:
    svc = fresh_service()
    svc.recompute_batch(1)
    items, _ = svc.list_impacts(batch_id=1, page=1, size=100)
    target = items[0]
    svc.review_impact(int(target["id"]), "人工已确认")
    # 触发同表重算（确认同表新版本不影响 META-1003；改用批次失效重算验证承接）
    import app.services.impact as impact_module

    batch = svc.get_batch(1)
    svc._invalidate_batch(batch)  # noqa: SLF001
    svc.recompute_batch(1)
    items2, _ = svc.list_impacts(batch_id=1, page=1, size=100)
    same_key = next(
        r for r in items2
        if (r["表编号"], r["来源对象编号"], r["影响原因"])
        == (target["表编号"], target["来源对象编号"], target["影响原因"])
    )
    assert same_key["复核状态"] == REVIEW_DONE and same_key["复核备注"] == "人工已确认"
    print("ok 10. 重算后承接上一版人工复核结论")
    _ = impact_module  # 保留导入说明


def main() -> None:
    test_fns = [obj for name, obj in sorted(globals().items()) if name.startswith("test_")]
    for fn in test_fns:
        fn()
    print(f"\n全部 {len(test_fns)} 项验收测试通过")


if __name__ == "__main__":
    main()
