"""影响范围重算 HTTP 接口级测试。

用 uvicorn 在随机端口起真实服务，仅用标准库 urllib 调用，覆盖：
- 批次重算 / 幂等 / 中断续算；
- 影响清单、看板、版本确认自动重算、复核；
- 元数据存储与依赖解析两处同源取数；
- 字面量路径不被 /{entry_id} 路由遮蔽。
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"


def call(method: str, path: str, payload: dict | None = None, expect_error: bool = False):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if not expect_error:
            raise
        return exc.code, json.loads(exc.read().decode())


def wait_ready() -> None:
    for _ in range(50):
        try:
            status, body = call("GET", "/api/health")
            if status == 200 and body.get("ok"):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("后端服务未在预期时间内就绪")


def main() -> None:
    config = uvicorn.Config("app.main:app", host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_ready()
    try:
        # 中断演练（批次2 第一张表前中断，HTTP 409）
        status, body = call(
            "POST",
            "/api/impact/batches/2/recompute",
            {"values": {"fail_after_tables": 1}},
            expect_error=True,
        )
        assert status == 409 and "中断" in body["detail"]
        status, body = call("GET", "/api/impact/batches")
        b2 = next(b for b in body["items"] if b["id"] == 2)
        assert b2["批次状态"] == "重算中" and b2["最后完整表"] == "META-1001"

        # 批次1 完整重算
        status, body = call("POST", "/api/impact/batches/1/recompute", {"values": {}})
        assert status == 200 and body["entry"]["批次状态"] == "已完成"
        first_count = body["entry"]["影响对象数"]
        # 幂等：再刷一次数量不变
        status, body = call("POST", "/api/impact/batches/1/recompute", {"values": {}})
        assert body["entry"]["影响对象数"] == first_count and "沿用现有" in body["message"]

        # 批次2 续算
        status, body = call("POST", "/api/impact/batches/2/recompute", {"values": {}})
        assert body["entry"]["批次状态"] == "已完成"

        # 清单分页与看板
        status, body = call("GET", "/api/impact/objects?size=3")
        assert status == 200 and len(body["items"]) == 3 and body["total"] >= 3
        status, board = call("GET", "/api/impact/board")
        reasons = [g["影响原因"] for g in board["按影响原因"]]
        assert reasons == sorted(reasons, key=["字段删除", "字段改名", "结构不一致",
                                               "模板别名失效", "新版本待确认"].index)

        # 版本确认 -> 自动重算
        status, body = call("POST", "/api/impact/versions/4/confirm")
        assert status == 200 and body["entry"]["确认状态"] == "已确认"
        status, body = call("GET", "/api/impact/objects?table_code=META-1002&size=100")
        reason_set = {r["影响原因"] for r in body["items"]}
        assert "字段删除" in reason_set and "新版本待确认" not in reason_set

        # 两处同源取数：依赖解析侧清单与元数据侧筛选 dependency 来源的记录一致
        status, meta_side = call("GET", "/api/metastore/impact-objects?size=100")
        status, dep_side = call("GET", "/api/dependency/review-queue?size=100")
        dep_ids = {r["id"] for r in dep_side["items"]}
        meta_dep_ids = {
            r["id"] for r in meta_side["items"] if r["来源模块"] == "dependency"
        }
        assert dep_ids == meta_dep_ids
        assert dep_side["total"] == len(meta_dep_ids)
        # 清单主键全局唯一
        all_ids = [r["id"] for r in meta_side["items"]]
        assert len(all_ids) == len(set(all_ids))
        sample = dep_side["items"][0]
        assert sample["待复核标志"] in (True, False)
        assert sample["最后一版元数据"]["id"] == sample["版本来源id"]

        # 最后一版元数据两处入口一致
        status, v1 = call("GET", "/api/metastore/META-1002/last-version")
        status, v2 = call("GET", "/api/impact/tables/META-1002/last-version")
        assert v1["id"] == v2["id"]

        # 复核动作
        target = next(r for r in meta_side["items"] if r["复核状态"] == "待复核")
        status, body = call(
            "POST",
            f"/api/impact/objects/{target['id']}/review",
            {"values": {"remark": "接口复核通过"}},
        )
        assert status == 200 and body["entry"]["复核状态"] == "已复核"

        # 路由未被 /{entry_id} 遮蔽
        status, body = call("GET", "/api/dependency/export")
        assert status == 200 and body["module"] == "dependency"
        status, body = call("GET", "/api/metastore/export")
        assert status == 200 and body["module"] == "metastore"

        # 原有业务模块口径未受影响
        status, body = call("GET", "/api/metastore?size=1")
        assert body["total"] == 3

        print("全部 HTTP 接口级断言通过")
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
