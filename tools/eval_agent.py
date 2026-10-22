# -*- coding: utf-8 -*-
"""Agent 评测跑批：路由准确率 + 工具调用 + 延迟 + token 成本。

两种模式：

    # ① 离线：只评「这句话被归到哪条路」，不调模型、不花钱，秒级出结果
    python tools/eval_agent.py

    # ② 端到端：真跑 /api/planner/plan，量工具序列、P50/P95 延迟、token 与成本
    python tools/eval_agent.py --live --base http://127.0.0.1:8000

为什么分两种：
  路由是**规则**判的（`routers/planner.py::route_of`），规则对不对不需要模型参与，
  70 条一遍跑完不到 1 秒；把它和「真调模型」混在一起，每改一个正则都要等几分钟、
  还要烧 token，最后就没人愿意跑了。评测只有足够便宜，才会真的被用起来。

输出：
  reports/agent_eval_<时间戳>.json  —— 全量结果，含每条失败样例
  控制台打一张汇总表

⚠️ 报告里的数字只能来自实测。没跑到的项就写「未测」，不要填 0 或估算值。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.request
import urllib.error
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # campus-ai-h/
BACKEND = os.path.join(ROOT, "backend")
CASES = os.path.join(BACKEND, "eval", "agent_cases.json")
HOLDOUT = os.path.join(BACKEND, "eval", "agent_cases_holdout.json")
REPORTS = os.path.join(ROOT, "reports")

sys.path.insert(0, BACKEND)


# --------------------------------------------------------------------------
#  课表夹具：让「问课」类用例有数据可查
# --------------------------------------------------------------------------
def fixture_courses(today: date | None = None) -> list:
    """造一份**本周**的课表（日期跟着今天走，否则「明天有课吗」永远查不到课）。

    刻意包含两门名字相近的课（高等数学 / 大学英语），用来验证
    「按课名问」不会串到别门课上。
    """
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())

    def mk(name, weekday, start, end, slot, room):
        d = monday + timedelta(days=weekday)
        return {"date": d.isoformat(), "type": "course", "slot_order": slot,
                "start": start, "end": end, "name": name, "teacher": "",
                "location": room, "building": room.split("-")[0], "weeks": ""}

    return [
        mk("高等数学", 3, "08:10", "08:55", 3, "博文楼-301"),
        mk("大学英语", 1, "10:00", "10:45", 5, "博文楼-202"),
        mk("计算机网络", 4, "14:00", "14:45", 9, "知行楼-105"),
    ]


# --------------------------------------------------------------------------
#  离线：路由评测
# --------------------------------------------------------------------------
def load_cases(path: str = CASES) -> list:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("cases") or []


def eval_routes(cases: list) -> dict:
    from routers.planner import route_of

    today = date.today()
    courses = fixture_courses(today)
    rows, hit = [], 0
    for c in cases:
        try:
            actual = route_of(c["q"], courses, today)
        except Exception as e:                     # noqa: BLE001
            actual = f"ERROR:{type(e).__name__}"
        ok = actual == c["route"]
        hit += 1 if ok else 0
        rows.append({
            "id": c["id"], "q": c["q"],
            "expect": c["route"], "actual": actual, "ok": ok,
        })
    total = len(rows)
    return {
        "rows": rows,
        "accuracy": round(hit / total, 4) if total else 0.0,
        "passed": hit, "total": total,
    }


def by_category(rows: list) -> dict:
    """按用例编号前缀分组看准确率 —— 哪一类退化了，一眼能看出来。"""
    groups: dict = {}
    for r in rows:
        g = r["id"][0]
        groups.setdefault(g, []).append(r)
    return {g: {"passed": sum(1 for r in v if r["ok"]), "total": len(v)}
            for g, v in sorted(groups.items())}


# --------------------------------------------------------------------------
#  在线：端到端
# --------------------------------------------------------------------------
def _post(url: str, body: dict, token: str, timeout: float = 240) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Campus-Token", token)
    req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"null")


def login(base: str, username: str, password: str) -> str:
    body = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(base + "/api/auth/login", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


def pct(values: list, p: float):
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((len(s) - 1) * p))))
    return round(s[k], 3)


def eval_live(cases: list, base: str, token: str, only: set, limit: int) -> dict:
    courses = fixture_courses()
    rows = []
    picked = [c for c in cases
              if (not only or c["id"][0] in only) and c["route"] in ("plan", "quick")]
    if limit:
        picked = picked[:limit]
    print(f"在线模式：待跑 {len(picked)} 条（跑 plan 与 quick 两类 —— "
          f"plan 会调模型，quick 是单工具模板化回答、只需几毫秒；"
          f"docs / explain / local_ask / schedule_add 走的是规则与本地逻辑，不在这里测）")

    for i, c in enumerate(picked, 1):
        t0 = time.time()
        rec = {"id": c["id"], "q": c["q"], "expect_route": c["route"]}
        try:
            r = _post(base + "/api/planner/plan",
                      {"goal": c["q"], "days": 7, "courses": courses, "history": []},
                      token)
            dt = (time.time() - t0) * 1000
            plan = (r.get("plan") or "")
            tools = [s.get("tool") for s in (r.get("trace") or [])]
            usage = r.get("usage") or {}
            rec.update({
                "ok": bool(plan),
                "latency_ms": round(dt, 1),
                "tools": tools,
                "usage": usage,
                "chars": len(plan),
                "head": plan[:120],
            })
            # 期望工具（JSON 里有 tools 字段才对；没有就跳过这一项）
            exp = c.get("tools") or []
            if exp:
                rec["tools_expect"] = exp
                # 分开看两件事：**选没选对**（集合）与**顺序对不对**（列表）。
                # 合成一个指标会把「顺序错了」和「多调了一个」混成同一个数字，
                # 报告里就说不清是哪一类问题。
                rec["tools_set_ok"] = set(tools) == set(exp)
                rec["tools_ok"] = tools == exp
                rec["tools_extra"] = [t for t in tools if t not in exp]
        except Exception as e:                      # noqa: BLE001
            rec.update({"ok": False, "error": f"{type(e).__name__}: {e}",
                        "latency_ms": round((time.time() - t0) * 1000, 1)})
        rows.append(rec)
        flag = "✓" if rec.get("ok") else "✗"
        print(f"  [{i}/{len(picked)}] {flag} {c['id']} {rec.get('latency_ms', 0)/1000:.1f}s "
              f"tools={rec.get('tools') or []}  {c['q'][:24]}")

    lat = [r["latency_ms"] for r in rows if r.get("latency_ms")]
    toks = [r["usage"].get("total_tokens", 0) for r in rows if r.get("usage")]
    costs = [r["usage"].get("cost_cny", 0) for r in rows if r.get("usage")]
    tools_rows = [r for r in rows if "tools_ok" in r]

    return {
        "rows": rows,
        "latency_p50_ms": pct(lat, 0.50),
        "latency_p95_ms": pct(lat, 0.95),
        "tokens_median": statistics.median(toks) if toks else None,
        "tokens_max": max(toks) if toks else None,
        "cost_median_cny": round(statistics.median(costs), 6) if costs else None,
        "cost_total_cny": round(sum(costs), 6) if costs else None,
        "tools_set_accuracy": (round(sum(1 for r in tools_rows if r.get("tools_set_ok")) / len(tools_rows), 4)
                               if tools_rows else None),
        "tools_order_accuracy": (round(sum(1 for r in tools_rows if r["tools_ok"]) / len(tools_rows), 4)
                                 if tools_rows else None),
        "tools_labeled": len(tools_rows),
        "extra_call_rate": (round(sum(1 for r in tools_rows if r["tools_extra"]) / len(tools_rows), 4)
                            if tools_rows else None),
        "answered": sum(1 for r in rows if r.get("ok")),
        "total": len(rows),
    }


# --------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Agent 评测")
    ap.add_argument("--live", action="store_true", help="端到端跑（真调模型，耗 token）")
    ap.add_argument("--base", default=os.environ.get("CAMPUS_BASE", "http://127.0.0.1:8000"))
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="zrc666")
    ap.add_argument("--only", default="", help="只跑指定前缀，如 D,R 或 X")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    cases = load_cases()
    print(f"用例 {len(cases)} 条：{CASES}\n")

    route = eval_routes(cases)
    print("=== 路由准确率（离线，规则判定）===")
    for g, v in by_category(route["rows"]).items():
        print(f"  {g} 类：{v['passed']}/{v['total']}")
    print(f"  合计：{route['passed']}/{route['total']} = {route['accuracy']*100:.1f}%\n")

    fails = [r for r in route["rows"] if not r["ok"]]
    if fails:
        print("路由失败样例：")
        for r in fails:
            print(f"  {r['id']}  期望 {r['expect']:<13} 实际 {r['actual']:<13} {r['q']}")
        print()

    # ---- 留出集：写完就不许改规则再回来测（改了就得把用例移进开发集）----
    hold = {"accuracy": None, "passed": 0, "total": 0, "failures": []}
    if os.path.exists(HOLDOUT):
        hcases = load_cases(HOLDOUT)
        hr = eval_routes(hcases)
        hold = {"accuracy": hr["accuracy"], "passed": hr["passed"],
                "total": hr["total"], "rows": hr["rows"],
                "failures": [r for r in hr["rows"] if not r["ok"]]}
        print("=== 留出集（未参与调参）===")
        print(f"  {hr['passed']}/{hr['total']} = {hr['accuracy']*100:.1f}%")
        for r in hold["failures"]:
            print(f"  ✗ {r['id']}  期望 {r['expect']:<13} 实际 {r['actual']:<13} {r['q']}")
        print()

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cases_file": CASES,
        "route": {"accuracy": route["accuracy"], "passed": route["passed"],
                  "total": route["total"], "by_category": by_category(route["rows"]),
                  "failures": fails},
        "holdout": hold,
    }

    if args.live:
        only = {x.strip().upper() for x in args.only.split(",") if x.strip()}
        token = login(args.base, args.user, args.password)
        live = eval_live(cases, args.base, token, only, args.limit)
        report["live"] = live
        print("\n=== 端到端（真跑接口）===")
        print(f"  回答成功率      {live['answered']}/{live['total']}")
        print(f"  P50 / P95 延迟  {live['latency_p50_ms']} / {live['latency_p95_ms']} ms")
        print(f"  单次 token 中位 {live['tokens_median']}（最大 {live['tokens_max']}）")
        print(f"  单次成本 中位   ¥{live['cost_median_cny']}（合计 ¥{live['cost_total_cny']}）")
        if live["tools_set_accuracy"] is not None:
            print(f"  工具选择准确率  {live['tools_set_accuracy']*100:.1f}%"
                  f"（标注 {live['tools_labeled']} 条）")
            print(f"  工具顺序正确率  {live['tools_order_accuracy']*100:.1f}%")
            print(f"  多余调用率      {live['extra_call_rate']*100:.1f}%")

    os.makedirs(REPORTS, exist_ok=True)
    name = "agent_eval_%s.json" % time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(REPORTS, name)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\n报告已写入 {out}")
    return 0 if route["accuracy"] >= 0.9 else 1


if __name__ == "__main__":
    sys.exit(main())
