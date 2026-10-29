"""
metrics.py: tokens, cost, speed and quality per tick for the Watchtower swarm (C5).

    m = Metrics()
    m.start_tick(tick_id, n_sectors, max_agents)
    m.add_usage(decision["usage"])        # swarm usage dict (has by_model)
    m.add_search(decision["searches"])    # You.com searches
    m.add_one_action()                    # each explicitly enabled Discord dispatch
    row = m.end_tick(action=..., score=..., false_alarm=..., escalated=..., agents_used=..., ...)
    m.summary(); m.save("metrics.json")
"""
import json
import time

# USD per 1M tokens (input, output). Sources: event page (Oct 26) + Parasail price list.
# Cached-input discounts are ignored, so costs are a slight upper bound.
PRICES = {
    "parasail-glm-53": (1.40, 4.40),
    "parasail-glm-53-flash": (0.15, 0.50),
    "parasail-minimax-m31-flash": (0.15, 0.60),
    "parasail-deepseek-v41-flash": (0.30, 1.20),
    "parasail-deepseek-v4-flash": (0.14, 0.28),
    "parasail-kimi-k3": (3.00, 15.00),
}
YDC_SEARCH_USD = 5 / 1000      # $5 per 1K search calls


def _model_cost(model: str, prompt: int, completion: int):
    price = PRICES.get(model)
    if not price:
        return 0.0, False
    p_in, p_out = price
    return (prompt * p_in + completion * p_out) / 1e6, True


class Metrics:
    def __init__(self):
        self.ticks: list[dict] = []
        self.cur: dict | None = None

    # ---------- per tick ----------
    def start_tick(self, tick_id, sectors: int, max_agents: int):
        self.cur = dict(tick_id=tick_id, sectors=sectors, max_agents=max_agents,
                        prompt_tokens=0, completion_tokens=0, llm_calls=0, by_model={},
                        searches=0, one_actions=0, t0=time.perf_counter())

    def add_usage(self, usage: dict | None):
        if not usage or self.cur is None:
            return
        c = self.cur
        c["prompt_tokens"] += usage.get("prompt_tokens", 0) or 0
        c["completion_tokens"] += usage.get("completion_tokens", 0) or 0
        c["llm_calls"] += usage.get("requests", 0) or 0
        for model, u in (usage.get("by_model") or {}).items():
            m = c["by_model"].setdefault(model, {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0})
            for k in m:
                m[k] += u.get(k, 0) or 0

    add_crew_usage = add_usage   # name used in the design doc

    def add_search(self, n: int = 1):
        if self.cur is not None:
            self.cur["searches"] += n or 0

    def add_one_action(self, n: int = 1):
        if self.cur is not None:
            self.cur["one_actions"] += n or 0

    def end_tick(self, action, score=None, false_alarm=None, escalated=False,
                 agents_used=0, research_skipped=False, source="", usage_complete=True) -> dict:
        """Finish a tick; flag costs when provider usage could not be counted fully."""
        c, self.cur = self.cur, None
        c["latency_s"] = round(time.perf_counter() - c.pop("t0"), 2)

        llm_cost, priced = 0.0, bool(usage_complete)
        if c["by_model"]:
            for model, u in c["by_model"].items():
                cost, known = _model_cost(model, u["prompt_tokens"], u["completion_tokens"])
                llm_cost += cost
                priced = priced and known
        elif c["prompt_tokens"] or c["completion_tokens"]:
            priced = False   # tokens without a model breakdown

        c.update(action=action, score=score, false_alarm=false_alarm, escalated=bool(escalated),
                 agents_used=agents_used, research_skipped=bool(research_skipped), source=source,
                 total_tokens=c["prompt_tokens"] + c["completion_tokens"],
                 cost_usd=round(llm_cost + c["searches"] * YDC_SEARCH_USD, 5),
                 cost_complete=priced,
                 within_budget=agents_used <= (c["max_agents"] if c["max_agents"] is not None else 99))
        self.ticks.append(c)
        self._print_tick(c)
        return c

    # ---------- reporting ----------
    @staticmethod
    def _print_tick(c):
        sc = "-" if c["score"] is None else f"{c['score']:.1f}"
        fa = "-" if c["false_alarm"] is None else ("YES" if c["false_alarm"] else "no")
        star = "" if c["cost_complete"] else "*"
        print(f"  METRICS tick {c['tick_id']}: {c['action']} | score {sc} | false alarm {fa} | "
              f"{c['total_tokens']:,} tok ({c['llm_calls']} calls) | {c['searches']} search | "
              f"${c['cost_usd']:.4f}{star} | {c['latency_s']}s | agents {c['agents_used']}/{c['max_agents']}")

    def totals(self) -> dict:
        t = self.ticks
        n = len(t) or 1
        scored = [x for x in t if x["score"] is not None]
        tokens = sum(x["total_tokens"] for x in t)
        cost = sum(x["cost_usd"] for x in t)
        perfect = sum(1 for x in scored if x["score"] == 1.0)
        by_model: dict = {}
        for x in t:
            for model, u in x["by_model"].items():
                m = by_model.setdefault(model, {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0})
                for k in m:
                    m[k] += u[k]
        return dict(
            ticks=len(t),
            scored_ticks=len(scored),
            avg_score=round(sum(x["score"] for x in scored) / len(scored), 3) if scored else None,
            perfect=perfect,
            false_alarms=sum(1 for x in t if x["false_alarm"]),
            escalations=sum(1 for x in t if x["escalated"]),
            discord_posts=sum(x["one_actions"] for x in t),
            total_tokens=tokens,
            tokens_per_tick=round(tokens / n),
            llm_calls=sum(x["llm_calls"] for x in t),
            total_cost_usd=round(cost, 4),
            cost_per_tick_usd=round(cost / n, 5),
            cost_complete=all(x["cost_complete"] for x in t),
            avg_latency_s=round(sum(x["latency_s"] for x in t) / n, 2),
            searches=sum(x["searches"] for x in t),
            research_skipped=sum(1 for x in t if x["research_skipped"]),
            budget_violations=sum(1 for x in t if not x["within_budget"]),
            by_model=by_model,
        )

    def summary(self) -> dict:
        s = self.totals()
        if not s["ticks"]:
            return s
        star = "" if s["cost_complete"] else "  (* excludes untracked usage or unknown model prices)"
        print("\n================ WATCHTOWER METRICS ================")
        print(f"Ticks handled        {s['ticks']}  (scored: {s['scored_ticks']})")
        if s["avg_score"] is not None:
            print(f"Avg score            {s['avg_score']}  ({s['perfect']} perfect)")
        print(f"False alarms         {s['false_alarms']}")
        print(f"Discord escalations  {s['escalations']}  (posted: {s['discord_posts']})")
        print(f"Total tokens         {s['total_tokens']:,}  ({s['tokens_per_tick']:,}/tick, {s['llm_calls']} calls)")
        print(f"Total cost           ${s['total_cost_usd']}  (${s['cost_per_tick_usd']}/tick){star}")
        print(f"Avg latency          {s['avg_latency_s']}s/tick")
        print(f"You.com searches     {s['searches']}  (research skipped on {s['research_skipped']} calm ticks)")
        print(f"Budget violations    {s['budget_violations']}")
        for model, u in s["by_model"].items():
            print(f"  {model:<30} {u['prompt_tokens'] + u['completion_tokens']:>8,} tok  {u['requests']} calls")
        print("===================================================")
        return s

    def save(self, path: str = "metrics.json"):
        with open(path, "w") as f:
            json.dump({"summary": self.totals(), "ticks": self.ticks}, f, indent=2, default=str)
        print(f"Saved {path}")
