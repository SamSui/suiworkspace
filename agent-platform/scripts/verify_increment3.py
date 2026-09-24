"""增量 3 运行级验证脚本（3.2 / 3.6 / 3.7）。

真实 Redis 实例（docker compose ap-redis）→ 起两个独立 uvicorn 进程（双副本共享 Redis）：
- 3.2 / 3.7 多实例一致性：A 实例发起 `hitl_required` 挂起，B 实例凭同 `thread_id` resume 成功；
  并发 N 请求跨两副本，全部 `done`，无状态丢失。
- 3.6 挂起后进程重启仍可恢复：独立实例 R 挂起 → 杀掉 → 起新进程 resume → 成功（checkpoint 生效）。
- 3.7 抛 P50/P95/P99 压测指标。

用法：
    .venv/Scripts/python.exe scripts/verify_increment3.py
要求：Redis 已在本机 6379 起（docker compose ap-redis）。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PORT_A, PORT_B = 8210, 8220
PORT_R0, PORT_R1 = 8230, 8231


def _proc(port: int) -> subprocess.Popen:
    env = dict(os.environ)
    env["REDIS_URL"] = env.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "langgraph_service.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait_healthy(base: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/healthz", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    raise RuntimeError(f"{base}/healthz 未就绪（checkpoint 不可用）")


def _kill(p: subprocess.Popen) -> None:
    try:
        p.terminate()
        p.wait(timeout=6)
    except Exception:  # noqa: BLE001
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass


async def _post_sse(client: httpx.AsyncClient, url: str, payload: dict[str, object]):
    out: list[dict[str, object]] = []
    async with client.stream("POST", url, json=payload) as resp:
        event, data = "message", ""
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
                try:
                    out.append({"event": event, "data": json.loads(data)})
                except ValueError:
                    pass
    return out


# ---------- 用例 ----------

async def case_single_flow(ca) -> dict[str, object]:
    tid = uuid.uuid4().hex[:32]
    evs = await _post_sse(ca, f"http://127.0.0.1:{PORT_A}/v1/stream",
                          {"thread_id": tid, "query": "介绍一下报销流程", "user_id": 1,
                           "kb_id": None})
    events = [e["event"] for e in evs]
    assert "token" in events and "done" in events, f"缺 token/done: {events}"
    seqs = [e["data"]["seq"] for e in evs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), f"seq 不单调: {seqs}"
    done = [e for e in evs if e["event"] == "done"][0]["data"]
    assert "message_id" in done and "usage" in done
    return {"case": "single_flow(A直答+SSE)", "ok": True, "n_events": len(evs),
            "done": done}


async def case_cross_instance_hitl(ca, cb) -> dict[str, object]:
    tid = uuid.uuid4().hex[:32]
    evs = await _post_sse(ca, f"http://127.0.0.1:{PORT_A}/v1/stream",
                          {"thread_id": tid, "query": "提交付款申请需审批", "user_id": 1,
                           "kb_id": None, "hitl_required": True})
    events = [e["event"] for e in evs]
    assert "interrupt" in events, f"应挂起 emit interrupt: {events}"
    evs2 = await _post_sse(cb, f"http://127.0.0.1:{PORT_B}/v1/resume",
                           {"thread_id": tid, "value": "approve"})
    events2 = [e["event"] for e in evs2]
    assert "done" in events2, f"B 实例 resume 应 done: {events2}"
    return {"case": "cross_instance_hitl(A挂起B恢复)", "ok": True, "thread_id": tid}


async def case_process_restart_resume() -> dict[str, object]:
    """独立实例 R0 挂起 → 杀 R0 → 新实例 R1 resume → 成功（3.6 checkpoint 跨进程）。"""
    tid = uuid.uuid4().hex[:32]
    r0 = _proc(PORT_R0)
    try:
        _wait_healthy(f"http://127.0.0.1:{PORT_R0}")
        async with httpx.AsyncClient(timeout=30) as c0:
            evs = await _post_sse(c0, f"http://127.0.0.1:{PORT_R0}/v1/stream",
                                  {"thread_id": tid, "query": "审批单", "user_id": 1,
                                   "kb_id": None, "hitl_required": True})
        assert "interrupt" in [e["event"] for e in evs], "R0 应挂起"
    finally:
        _kill(r0)  # 杀掉 → 进程重启（checkpoint 在 Redis，不丢）

    r1 = _proc(PORT_R1)
    try:
        _wait_healthy(f"http://127.0.0.1:{PORT_R1}")
        async with httpx.AsyncClient(timeout=30) as c1:
            evs2 = await _post_sse(c1, f"http://127.0.0.1:{PORT_R1}/v1/resume",
                                   {"thread_id": tid, "value": "approve"})
        assert "done" in [e["event"] for e in evs2], "重启后 resume 应 done"
    finally:
        _kill(r1)
    return {"case": "process_restart_resume(R挂起→杀进程→新进程恢复)", "ok": True,
            "thread_id": tid}


async def case_concurrency(ca, cb) -> dict[str, object]:
    N = 40
    lats: list[float] = []
    done_ok = 0
    fails: list[str] = []
    base_a, base_b = f"http://127.0.0.1:{PORT_A}", f"http://127.0.0.1:{PORT_B}"

    async def one(idx: int):
        base = base_a if idx % 2 == 0 else base_b
        cli = ca if idx % 2 == 0 else cb
        tid = uuid.uuid4().hex[:32]
        t0 = time.perf_counter()
        try:
            evs = await _post_sse(cli, f"{base}/v1/stream",
                                  {"thread_id": tid, "query": f"问题 {idx}", "user_id": idx,
                                   "kb_id": None})
            if [e["event"] for e in evs].count("done") == 1:
                nonlocal done_ok
                done_ok += 1
            else:
                fails.append(f"{tid}: no done")
        except Exception as exc:  # noqa: BLE001
            fails.append(f"{tid}: {exc}")
        finally:
            lats.append((time.perf_counter() - t0) * 1000)

    await asyncio.gather(*(one(i) for i in range(N)))
    lat = sorted(lats)
    p50 = lat[len(lat) // 2]
    p95 = lat[int(len(lat) * 0.95)]
    p99 = lat[int(len(lat) * 0.99)]
    return {"case": f"concurrency({N}并发/双副本)", "ok": done_ok == N,
            "done": done_ok, "total": N, "fails": len(fails),
            "p50_ms": round(p50, 1), "p95_ms": round(p95, 1), "p99_ms": round(p99, 1)}


async def main() -> int:
    procs = [_proc(PORT_A), _proc(PORT_B)]
    results: list[dict[str, object]] = []
    try:
        for _p, port in zip(procs, (PORT_A, PORT_B), strict=True):
            _wait_healthy(f"http://127.0.0.1:{port}")
        async with httpx.AsyncClient(timeout=60) as ca, httpx.AsyncClient(timeout=60) as cb:
            results.append(await case_single_flow(ca))
            results.append(await case_cross_instance_hitl(ca, cb))
            results.append(await case_concurrency(ca, cb))
        # 进程重启用例需独占两级端口，放最后
        results.append(await case_process_restart_resume())
    finally:
        for p in procs:
            _kill(p)

    all_ok = True
    print("[增量3] 运行级验证汇总（真实 Redis 双副本）")
    for r in results:
        ok = bool(r.pop("ok"))
        all_ok = all_ok and ok
        rest = {k: v for k, v in r.items() if k != "case"}
        print(f"  {'PASS' if ok else 'FAIL'} {r['case']}: {json.dumps(rest, ensure_ascii=False)}")
    print(f"\n结论：{'全部 PASS' if all_ok else '存在 FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))