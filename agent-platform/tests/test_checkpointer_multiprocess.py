"""3.2 Checkpoint 接入单测 —— **多进程**读写同一 `thread_id` 状态一致（用真实 Redis，非 mock）。

三个独立子进程（各自独立连接池、独立 RedisSaver 实例）共享同一 Redis 与同一
`thread_id`：一个写入 checkpoint、另两个独立读出——验证「多实例读写同一 thread_id
状态一致」，且写入对写入方之外的进程可见（这是 RedisSaver 相对 InMemorySaver 的核心价值）。

真实 Redis 必须在 `REDIS_URL`（默认 redis://127.0.0.1:6379/0）可达；否则跳过。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

import pytest

import redis as redislib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")


def _redis_down() -> bool:
    try:
        r = redislib.from_url(REDIS_URL, socket_timeout=2)
        r.ping()
        return False
    except Exception:  # noqa: BLE001
        return True


WORKER = """
import asyncio, json, sys
import redis.asyncio as aio
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import END, StateGraph

async def main():
    kind, thread, mode, url = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    # 独立进程 = 独立连接池 + 独立 checkpointer 实例（与生产 build_checkpointer 同路径）
    client = aio.from_url(url, decode_responses=False)
    cp = AsyncRedisSaver(redis_client=client)
    await cp.asetup()
    if kind == "put":
        async def w(state):
            return {"answer": mode}
        g = StateGraph(dict)
        g.add_node("w", w)
        g.add_edge("w", END)
        g.set_entry_point("w")
        app = g.compile(checkpointer=cp)
        await app.ainvoke({"q": mode}, {"configurable": {"thread_id": thread}})
        print(json.dumps({"has": True, "answer": mode}, ensure_ascii=False))
    elif kind == "get":
        tup = await cp.aget_tuple({"configurable": {"thread_id": thread}})
        vals = (tup.checkpoint.get("channel_values") or {}) if tup else {}
        # 兼容两种通道形状：顶层 answer 或 __root__ 包裹
        answer = vals.get("answer") or (vals.get("__root__") or {}).get("answer")
        print(json.dumps({"has": bool(tup and tup.checkpoint), "answer": answer}, ensure_ascii=False))
    await client.aclose()

asyncio.run(main())
"""


def _spawn(args: list[str]) -> dict:
    r = subprocess.run(
        [sys.executable, "-c", WORKER, *args],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        pytest.fail(f"worker 失败: {r.stderr[-800:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(
    _redis_down(), reason="真实 Redis 不可达，跳过（需 ap-redis 运行中）"
)
def test_multiprocess_shared_thread_id_state():
    """三个独立进程共享同一 thread_id：一个写入，两个独立读出且读到一致的值。"""
    # 用唯一 thread_id：既避免与共享 Redis 上他人 checkpoint 冲突、也无需清理（幂等可重跑）
    thread = f"mptest-{uuid.uuid4().hex[:12]}"
    expected = "hello-from-proc-1"

    # 进程 1 写入
    put = _spawn(["put", thread, expected, REDIS_URL])
    assert put["has"] and put["answer"] == expected

    # 进程 2、进程 3 各自独立读：应看到进程 1 写入的同一值（状态一致）
    for i in range(2):
        got = _spawn(["get", thread, "x", REDIS_URL])
        assert got["has"], f"进程 {i + 2} 应能读到进程 1 写入的 checkpoint"
        assert got["answer"] == expected, f"进程 {i + 2} 读到的值不一致: {got}"