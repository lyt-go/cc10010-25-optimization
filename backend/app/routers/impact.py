"""影响范围重算接口。

围绕过期批次驱动的影响对象清单提供：
- 过期批次重算（幂等、可中断续算）、批次进度查询；
- 影响对象清单查询与人工复核；
- 元数据版本确认（确认后相关批次按新结构自动重算）、最后一版元数据查询；
- 按影响原因排列的概览看板。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.impact import impact_service

router = APIRouter(prefix="/api/impact", tags=["影响范围重算"])


@router.get("/batches")
def list_batches() -> dict:
    """过期批次列表：含重算进度与最后完整表，用于判断续算位置。"""
    return {"items": impact_service.list_batches()}


@router.post("/batches/{batch_id}/recompute", response_model=ActionResult)
def recompute_batch(batch_id: int, payload: EntryPayload | None = None) -> ActionResult:
    """重算指定过期批次。

    重复刷新已完成批次只返回现有清单（一组影响对象）；中断后的批次从
    最后完整元数据表之后续算。传 fail_after_tables 可演练断点恢复。
    """
    fail_after = None
    if payload and payload.values:
        raw = payload.values.get("fail_after_tables")
        if raw is not None:
            try:
                fail_after = int(raw)
            except (TypeError, ValueError):
                fail_after = None
    try:
        batch, message = impact_service.recompute_batch(
            batch_id, fail_after_tables=fail_after
        )
    except Exception as exc:  # 演练断点时把可读信息返回给前端
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.get("/objects", response_model=PageResult[dict])
def list_impacts(
    batch_id: int | None = Query(default=None, description="按过期批次过滤"),
    table_code: str | None = Query(default=None, description="按表编号过滤"),
    table_name: str | None = Query(default=None, description="按表名模糊过滤"),
    reason: str | None = Query(default=None, description="按影响原因过滤"),
    review_status: str | None = Query(default=None, description="待复核/已复核"),
    source_module: str | None = Query(default=None, description="dependency/sqltpl"),
    relation_no: str | None = Query(default=None, description="按依赖关系编号过滤"),
    sql_tpl_no: str | None = Query(default=None, description="按SQL模板编号过滤"),
    keyword: str | None = Query(default=None, description="按对象编号/目标对象检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """影响对象数据清单：元数据存储与依赖解析两处都从这里取数。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = impact_service.list_impacts(
        batch_id=batch_id,
        table_code=table_code,
        table_name=table_name,
        reason=reason,
        review_status=review_status,
        source_module=source_module,
        relation_no=relation_no,
        sql_tpl_no=sql_tpl_no,
        keyword=keyword,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/objects/{impact_id}/review", response_model=ActionResult)
def review_impact(impact_id: int, payload: EntryPayload) -> ActionResult:
    """把影响对象标记为已复核，可带复核备注。"""
    remark = str(payload.values.get("remark") or "").strip() or None
    row, message = impact_service.review_impact(impact_id, remark)
    if row is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=row)


@router.get("/board")
def impact_board() -> dict:
    """概览看板：按影响原因排列，附批次重算进度与待复核量。"""
    return impact_service.impact_board()


@router.get("/versions", response_model=PageResult[dict])
def list_versions(
    keyword: str | None = Query(default=None, description="按表编号/表名检索"),
    status: str | None = Query(default=None, description="按确认状态过滤"),
    table_code: str | None = Query(default=None, description="按表编号过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """元数据版本列表。"""
    items, total = impact_service.list_versions(
        keyword=keyword, status=status, table_code=table_code, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/versions/{version_id}")
def get_version(version_id: int) -> dict:
    """读取单版元数据记录。"""
    version = impact_service.get_version(version_id)
    if version is None:
        raise HTTPException(status_code=404, detail=f"元数据版本 {version_id} 不存在")
    return version


@router.get("/tables/{table_code}/last-version")
def get_last_version(table_code: str) -> dict:
    """详情用：呈现一张表的最后一版元数据记录。"""
    version = impact_service.get_last_version(table_code)
    if version is None:
        raise HTTPException(status_code=404, detail=f"表 {table_code} 暂无元数据版本")
    return version


@router.post("/versions/{version_id}/confirm", response_model=ActionResult)
def confirm_version(version_id: int) -> ActionResult:
    """确认新版本：确认状态、表结构与影响清单统一到该版本来源并重算。"""
    version, message = impact_service.confirm_version(version_id)
    if version is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=version)


@router.get("/dependency-reviews", response_model=PageResult[dict])
def dependency_reviews(
    relation_no: str | None = Query(default=None, description="按依赖关系编号过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """依赖解析侧视图：读同一份影响清单并标出待复核关系。"""
    items, total = impact_service.list_dependency_reviews(
        relation_no=relation_no, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_impacts() -> dict:
    """导出全量影响对象数据清单（不分页），两处取数同源。"""
    items, total = impact_service.list_impacts(page=1, size=10000)
    return {"module": "impact_object", "total": total, "items": items}
