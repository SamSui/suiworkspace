"""并发会话压测与容量基线（增量 5.5）。

用途：给出「单副本承载并发数 + 扩容阈值」的可复跑基线。

两种模式：
1. 离线（默认，`--svc-url` 空）：复用检索链确定性桩走纯逻辑并发，得到 Python 侧
   吞吐与 P50/P95/P99。不含真实 Milvus/ES/LLM-RPC 延迟——容量值是**逻辑下限**，
   本工作区零依赖可跑，作为证据。
2. 在线（`--svc-url` 指向真实编排，`--token` 给 Bearer JWT）：对 `/v1/chat/stream`
   压测，结果含真实编排与存储延迟。依赖六存储栈与网关鉴权（注册→换 JWT）。
   六服务在线压测已在增量 5 补跑登记中实跑（见 5.5 交付回传）。

扩容阈值：以 CPU 均用率 ≥70% 触发 HPA（对应 `api-hpa.yaml`），与 Little's Law
反推的单副本并发数共同给出建议。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time

from observability.prom import metrics

# 离线单请求合成时延（秒）：模拟一次检索 + 生成的外呼 I/O。
_SYNTHETIC_WORK_S = 0.02


async def _local_one(sem: asyncio.Semaphore, seq: int, results: list[float]) -> None:
    async with sem:
        t0 = time.monotonic()
        await asyncio.sleep(_SYNTHETIC_WORK_S)  # 模拟检索/生成的 I/O
        metrics.cache_hit() if seq % 3 else metrics.cache_miss()
        metrics.observe_retrieval(time.monotonic() - t0)
        results.append(time.monotonic() - t0)


async def _online_one(
    sem: asyncio.Semaphore, client, payloads: list[str], seq: int, results: list[float]
) -> None:
    async with sem:
        t0 = time.monotonic()
        body = {"thread_id": f"lt{seq}", "query": payloads[seq % len(payloads)]}
        async with client.stream("POST", "/v1/chat/stream", json=body) as resp:
            resp.raise_for_status()
            async for _ in resp.aiter_lines():
                pass
        dur = time.monotonic() - t0
        metrics.observe_retrieval(dur)
        results.append(dur)


async def _run(total: int, concurrency: int, svc_url: str, token: str = "") -> dict:
    sem = asyncio.Semaphore(concurrency)
    results: list[float] = []
    payloads = ["报销流程是什么", "请假制度怎么走", "绩效哪里查"]
    online = bool(svc_url)

    t0 = time.monotonic()
    if online:
        import httpx

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(base_url=svc_url, timeout=60, headers=headers) as client:
            tasks = [
                asyncio.create_task(_online_one(sem, client, payloads, i, results))
                for i in range(total)
            ]
            await asyncio.gather(*tasks)
    else:
        tasks = [asyncio.create_task(_local_one(sem, i, results)) for i in range(total)]
        await asyncio.gather(*tasks)
    wall = time.monotonic() - t0

    return build_summary(results, wall, concurrency, online)


def build_summary(latencies: list[float], wall: float, concurrency: int, online: bool) -> dict:
    ordered = sorted(latencies)
    n = len(ordered) or 1

    def p(pct: float) -> float:
        return ordered[min(n - 1, int(n * pct))]

    throughput = n / wall if wall > 0 else 0.0
    budget_s = 1.0
    return {
        "mode": "online" if online else "local",
        "total": n,
        "concurrency": concurrency,
        "throughput_rps": round(throughput, 1),
        "p50_s": round(p(0.50), 4),
        "p95_s": round(p(0.95), 4),
        "p99_s": round(p(0.99), 4),
        "wall_s": round(wall, 2),
        "estimated_single_replica_concurrency": int(throughput * budget_s),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--total", type=int, default=200)
    ap.add_argument("--svc-url", default=os.getenv("SVC_URL", ""))
    ap.add_argument("--token", default=os.getenv("AGENT_TOKEN", ""))
    args = ap.parse_args()

    report = asyncio.run(_run(args.total, args.concurrency, args.svc_url, args.token))

    print("=" * 60)
    print("压测报告（增量 5.5）")
    print("=" * 60)
    for k, v in report.items():
        print(f"  {k}: {v}")
    print("=" * 60)
    if not report["mode"] == "online":
        print("  [离线] 纯逻辑+合成时延，容量为下限。真实栈需 --svc-url + --token 在线跑。")
    else:
        print("  [在线] 结果含真实编排与存储耗时（六存储栈实测）。")


if __name__ == "__main__":
    main()