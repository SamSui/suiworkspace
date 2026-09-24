"""在线六栈压测驱动（增量 5.5 未跑项补跑）· 多身份版

背景：网关按 user_id 限流 120 req / 60s
（api/middlewares/ratelimit.py DEFAULT_LIMIT=120, WINDOW=60）。
单身份压测在 >120 req/60s 时对 429 —— 那是**每用户配额**，不是单副本容量上限。
为测出「单副本在真实编排+存储延迟下能承载的并发」，本驱动注册 N 用户、每用户独立 JWT，
压力按用户轮转，令每用户远低于 120/60s，从而逼近网关**聚合并发上限**。
统计口径复用 load_test.build_summary（P50/P95/P99、吞吐、Little's Law 单副本并发）。
"""

from __future__ import annotations

import argparse
import asyncio
import time
import uuid

import httpx
from load_test import build_summary


async def register_user(client: httpx.AsyncClient) -> str:
    name = f"olt_user_{uuid.uuid4().hex[:8]}"
    r = await client.post("/v1/users", json={"name": name})
    r.raise_for_status()
    api_key = r.json()["api_key"]
    r = await client.post("/v1/auth/token", json={"name": name, "api_key": api_key})
    r.raise_for_status()
    return r.json()["access_token"]


async def _one(sem, client, payloads, seq, results, errors) -> None:
    async with sem:
        t0 = time.monotonic()
        body = {"thread_id": f"olt{seq}", "query": payloads[seq % len(payloads)]}
        try:
            async with client.stream("POST", "/v1/chat/stream", json=body) as resp:
                if resp.status_code >= 400:
                    errors.append((resp.status_code, seq))
                    return
                resp.raise_for_status()
                async for _ in resp.aiter_lines():
                    pass
            results.append(time.monotonic() - t0)
        except Exception as exc:  # noqa: BLE001
            errors.append((type(exc).__name__, seq))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--svc-url", default="http://127.0.0.1:8000")
    ap.add_argument("--users", type=int, default=40)
    ap.add_argument("--concurrency", type=int, default=60)
    ap.add_argument("--total", type=int, default=600)
    args = ap.parse_args()

    payloads = ["报销流程是什么", "请假制度怎么走", "绩效哪里查"]

    async with httpx.AsyncClient(base_url=args.svc_url, timeout=60) as admin:
        tokens = await asyncio.gather(*[register_user(admin) for _ in range(args.users)])

    sem = asyncio.Semaphore(args.concurrency)
    results: list[float] = []
    errors: list[tuple] = []

    async def worker(uidx: int) -> None:
        tok = tokens[uidx % len(tokens)]
        my_indices = [i for i in range(args.total) if i % len(tokens) == uidx % len(tokens)]
        async with httpx.AsyncClient(
            base_url=args.svc_url,
            headers={"Authorization": f"Bearer {tok}"},
            timeout=60,
        ) as c:
            for seq in my_indices:
                await _one(sem, c, payloads, seq, results, errors)

    t0 = time.monotonic()
    await asyncio.gather(*[worker(u) for u in range(len(tokens))])
    wall = time.monotonic() - t0

    report = build_summary(results, wall, args.concurrency, online=True)
    print("=" * 64)
    print("六服务在线压测报告（多身份 · 增量 5.5 补跑）")
    print("=" * 64)
    for k, v in report.items():
        print(f"  {k}: {v}")
    print(f"  users: {len(tokens)}")
    print(f"  errors: {errors[:10]} (total {len(errors)})")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())