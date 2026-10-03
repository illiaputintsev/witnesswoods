"""Forest Witness agent: a hand-written tool loop over the Anthropic Messages API.

  python -m fw.agent --lannr 20 --n 10            investigate the N newest regeneration-felling notifications
  python -m fw.agent A_41029-2026 ...              investigate specific notifications

Route (FW_ROUTE=condense|direct) changes only the base URL and Condense headers; model, prompts,
tools and parameters are identical, so the two routes can be compared fairly.
"""
import argparse
import json
import os
import time
import uuid

import anthropic

from fw import config, skogs, tools

SESSION_SIZE = 8          # notifications per conversation, then a fresh session with a carry-over note
MAX_TOOL_CALLS = 12       # per notification; above this the harness records UNDER_SURVEYED
MAX_NUDGES = 2            # times we remind the model to call record_finding before forcing
MAX_TOKENS = 8000         # adaptive thinking shares this budget, so it is larger than the brief's 1,500
EFFORT = "medium"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
LOG = config.OUT / "log.jsonl"
STATE = config.OUT / "state.json"


def system_prompt() -> str:
    """Stable prefix: identical bytes on every request so it caches."""
    sysmd = (config.ROOT / "prompts" / "system.md").read_text()
    rubric = (config.ROOT / "prompts" / "rubric.md").read_text()
    return f"{sysmd}\n\n<rubric.md>\n{rubric}</rubric.md>\n"


def make_client(route: str, session_id: str) -> anthropic.Anthropic:
    key = os.environ["ANTHROPIC_API_KEY"]
    if route == "condense":
        return anthropic.Anthropic(api_key=key, base_url=config.CONDENSE_BASE_URL, default_headers={
            "X-Condense-Auth-Token": os.environ["CONDENSE_API_TOKEN"], "X-Condense-Session-Id": session_id})
    return anthropic.Anthropic(api_key=key)


def cost_usd(model: str, u: dict) -> float:
    p = config.PRICES[model]
    return (u["input"] * p["input"] + u["output"] * p["output"]
            + u["cache_write"] * p["cache_write"] + u["cache_read"] * p["cache_read"]) / 1e6


def _usage(resp) -> dict:
    u = resp.usage
    return {"input": u.input_tokens or 0, "output": u.output_tokens or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0}


def _short(args: dict) -> dict:
    return {k: (v if len(json.dumps(v, ensure_ascii=False)) < 80 else "...") for k, v in args.items()}


class Run:
    def __init__(self, route: str, model: str, budget: float, label: str = ""):
        self.route, self.model, self.budget = route, model, budget
        self.run_id = time.strftime("%Y%m%d-%H%M%S") + (f"-{label}" if label else "")
        self.totals = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "calls": 0, "usd": 0.0}
        self.lines, self.sessions, self.results = [], [], []
        self.status, self.progress, self.current = "starting", "", ""
        # One breakpoint only: the top-level auto marker on the last block caches tools + system + history,
        # because history is append-only. Condense adds breakpoints of its own and the API allows 4 in total.
        self.system = [{"type": "text", "text": system_prompt()}]
        config.OUT.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ logging
    def log(self, event: dict, line: str | None = None) -> None:
        event = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "run_id": self.run_id, "route": self.route, **event}
        with LOG.open("a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        if line:
            self.lines.append(f"{event['ts'][11:]} {line}")
            print(line, flush=True)
            self.state()

    def state(self, status: str | None = None, progress: str | None = None, current: str | None = None) -> None:
        """Live progress for the UI; written atomically so a reader never sees half a file."""
        self.status = status or self.status
        self.progress = progress if progress is not None else self.progress
        self.current = current if current is not None else self.current
        tmp = STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"run_id": self.run_id, "route": self.route, "model": self.model,
                                   "status": self.status, "progress": self.progress, "current": self.current,
                                   "sessions": self.sessions, "totals": self.totals,
                                   "log": self.lines[-80:]}, ensure_ascii=False, indent=1))
        os.replace(tmp, STATE)

    # ------------------------------------------------------------ model call
    def call(self, client, messages: list, beteckn: str, session_id: str):
        t0 = time.time()
        params = dict(model=self.model, max_tokens=MAX_TOKENS, system=self.system, tools=tools.SCHEMAS,
                      messages=messages, output_config={"effort": EFFORT}, betas=[FALLBACK_BETA], fallbacks="default")
        try:
            resp = client.beta.messages.create(**params, cache_control={"type": "ephemeral"})  # auto-caches history
        except anthropic.BadRequestError as e:
            if "cache_control" not in str(e):
                raise
            # Too many breakpoints once a proxy adds its own: retry once without ours, and log it
            self.log({"type": "cache_marker_retry", "notification": beteckn, "error": str(e)[:300]})
            resp = client.beta.messages.create(**params)
        u = _usage(resp)
        usd = cost_usd(self.model, u)
        for k in ("input", "output", "cache_write", "cache_read"):
            self.totals[k] += u[k]
        self.totals["calls"] += 1
        self.totals["usd"] = round(self.totals["usd"] + usd, 4)
        self.log({"type": "model_call", "notification": beteckn, "session_id": session_id, "usage": u,
                  "usd": round(usd, 5), "stop_reason": resp.stop_reason, "served_by": resp.model,
                  "latency_s": round(time.time() - t0, 2), "request_id": resp._request_id})
        return resp

    # ------------------------------------------------------------ one notification
    def investigate(self, client, messages: list, beteckn: str, session_id: str, intro: str = "") -> dict:
        start = len(messages)
        messages.append({"role": "user", "content": f"{intro}Investigate felling notification {beteckn}. "
                                                    f"You have at most {MAX_TOOL_CALLS} tool calls for it, "
                                                    "including record_finding. Finish by calling record_finding once."})
        path, n_calls, nudges, warned = [], 0, 0, False
        species_queried = False  # the rubric hint is shown only once the agent has looked at species itself
        meta = {"model": self.model, "route": self.route, "run_id": self.run_id, "session_id": session_id}
        while True:
            if self.totals["usd"] >= self.budget:
                raise BudgetExceeded(self.totals["usd"])
            resp = self.call(client, messages, beteckn, session_id)
            if resp.stop_reason == "refusal":
                del messages[start:]  # drop this notification's turns; the earlier prefix stays byte-identical
                return self.force(beteckn, path, meta, "the model declined to continue on this notification")
            messages.append({"role": "assistant", "content": resp.content})  # unchanged, thinking blocks included
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses:
                nudges += 1
                if nudges > MAX_NUDGES:
                    return self.force(beteckn, path, meta, "the agent did not call record_finding")
                messages.append({"role": "user", "content": "Finish this notification by calling record_finding."})
                continue
            results, done = [], None
            species_queried = species_queried or any(b.name == "query_species" for b in uses)
            for b in uses:
                n_calls += 1
                args = dict(b.input)
                path.append({"tool": b.name, "args": _short(args)})
                try:
                    if args.get("beteckn", beteckn).replace("_", " ") != beteckn:
                        out = {"ok": False, "errors": [f"This turn is for {beteckn}; call tools for that notification only."]}
                    elif b.name == "record_finding":
                        out = tools.record_finding(**args, _meta={**meta, "tool_path": path})
                        if out.get("ok"):
                            done = out
                    else:
                        out = tools.FUNCS[b.name](**args)
                        if b.name == "observation_effort" and not species_queried:
                            out["rubric_hint"] = "shown after you call query_species for this site"
                    is_err = isinstance(out, dict) and out.get("ok") is False
                except Exception as e:  # bad arguments or a data error: tell the agent, keep going
                    out, is_err = {"error": f"{type(e).__name__}: {e}"}, True
                self.log({"type": "tool_call", "notification": beteckn, "session_id": session_id, "tool": b.name,
                          "args": _short(args), "error": is_err},
                         f"{beteckn}  {b.name}({', '.join(f'{k}={v}' for k, v in _short(args).items() if k != 'beteckn')})"
                         + ("  -> rejected" if is_err else ""))
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": is_err,
                                "content": json.dumps(out, ensure_ascii=False, sort_keys=True, separators=(",", ":"))})
            if not done and n_calls >= MAX_TOOL_CALLS - 1 and not warned:
                # One warning turn before the cap: a text block after the tool results, same user message
                results.append({"type": "text", "text": f"Tool budget nearly used ({n_calls}/{MAX_TOOL_CALLS}). "
                                                        "Call record_finding now with the evidence you have."})
                warned = True
            messages.append({"role": "user", "content": results})
            if done:
                return {"beteckn": beteckn, "priority": done["priority"], "forced": False, "tool_calls": n_calls,
                        "path": [p["tool"] for p in path]}
            if n_calls >= MAX_TOOL_CALLS and warned and results[-1].get("type") != "text":
                messages.append({"role": "user", "content": "Tool-call cap reached for this notification; the "
                                                            "harness recorded it. Stop here."})
                return self.force(beteckn, path, meta, f"tool-call cap of {MAX_TOOL_CALLS} reached")

    def force(self, beteckn: str, path: list, meta: dict, why: str) -> dict:
        """Harness-written dossier when the agent does not finish, so a stuck run never hides a site.

        BRIEF 4.3 says UNDER_SURVEYED. We keep a HIGH, MEDIUM or ALREADY_FELLED rubric hint instead, so a
        harness failure can never demote a site the deterministic rubric already flags.
        """
        p = tools._profile(beteckn)
        hint = tools.rubric.hint(p)
        keep = hint["priority"] in ("HIGH", "MEDIUM", "ALREADY_FELLED")
        priority = hint["priority"] if keep else "UNDER_SURVEYED"
        ids = [p["notification"]["evidence_id"], *hint["evidence_ids"]]
        claim = (f"Investigation incomplete: {why}. Priority taken from the deterministic rubric hint ({hint['rule']})."
                 if keep else f"Investigation incomplete: {why}. Recorded as under-surveyed by the harness.")
        out = tools.record_finding(
            beteckn, priority, [{"claim": claim, "evidence_ids": ids}],
            "Review this notification manually; the automated investigation did not finish.",
            uncertainties=[f"The agent's investigation did not complete ({why})."],
            not_established=["This run did not complete an investigation of this site's species or habitat."],
            override_reason=None if priority == hint["priority"] else
            {"text": f"Harness fallback: {why}.", "evidence_ids": ids},
            _meta={**meta, "tool_path": path, "forced": why})
        self.log({"type": "forced", "notification": beteckn, "why": why, "result": out},
                 f"{beteckn}  FORCED {priority} ({why})")
        return {"beteckn": beteckn, "priority": out.get("priority", priority), "forced": True,
                "tool_calls": len(path), "path": [x["tool"] for x in path]}

    # ------------------------------------------------------------ batch
    def batch(self, notifications: list[str]) -> list[dict]:
        total = len(notifications)
        self.state("running", f"0/{total}")
        try:
            for s in range(0, total, SESSION_SIZE):
                chunk = notifications[s:s + SESSION_SIZE]
                session_id = f"ww-{self.run_id}-s{len(self.sessions) + 1}"
                self.sessions.append(session_id)
                client = make_client(self.route, session_id)
                messages: list = []
                intro = self.carry_over() if self.results else ""
                for i, b in enumerate(chunk):
                    self.state("running", f"{len(self.results)}/{total}", b)
                    res = self.investigate(client, messages, b, session_id, intro if i == 0 else "")
                    self.results.append(res)
                    self.log({"type": "finding", "notification": b, **res, "totals": self.totals},
                             f"{b}  => {res['priority']}{' (forced)' if res['forced'] else ''}  "
                             f"[{res['tool_calls']} tool calls]  running cost ${self.totals['usd']:.3f}")
            self.state("done", f"{len(self.results)}/{total}")
        except BudgetExceeded as e:
            self.log({"type": "budget_stop", "usd": e.usd}, f"STOPPED: budget ${self.budget} reached (${e.usd:.3f})")
            self.state("stopped_budget", f"{len(self.results)}/{total}")
        except anthropic.APIError as e:
            self.log({"type": "error", "notification": self.current, "error": f"{type(e).__name__}: {e}"[:500],
                      "request_id": getattr(e, "request_id", None)}, f"STOPPED: API error on {self.current}: "
                                                                    f"{type(e).__name__}")
            self.state("error", f"{len(self.results)}/{total}")
        return self.results

    def carry_over(self) -> str:
        """Five-line note that starts a new session; the system prompt stays byte-identical."""
        from collections import Counter
        c = Counter(r["priority"] for r in self.results)
        return ("Carry-over from the previous session of this batch:\n"
                f"- {len(self.results)} notifications already recorded: {dict(c)}.\n"
                "- Each notification is independent; do not reuse evidence IDs across notifications.\n"
                "- Keep following the rubric hint unless evidence gives a concrete reason to override.\n"
                "- Absence of records is not absence of species.\n\n")


class BudgetExceeded(Exception):
    def __init__(self, usd: float):
        self.usd = usd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("beteckn", nargs="*", help="notification IDs; underscores allowed instead of spaces")
    ap.add_argument("--lannr", default="20")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--route", default=config.FW_ROUTE, choices=("condense", "direct"))
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    ids = [b.replace("_", " ") for b in a.beteckn] or [n["beteckn"] for n in skogs.latest_notifications(a.lannr, a.n)]
    for b in ids:  # warm the data cache and evidence store before spending tokens
        tools._profile(b)
    run = Run(a.route, config.FW_MODEL, config.FW_BUDGET_USD, a.label)
    print(f"run {run.run_id}: {len(ids)} notifications, model {run.model}, route {run.route}, budget ${run.budget}")
    res = run.batch(ids)
    print(json.dumps({"run_id": run.run_id, "sessions": run.sessions, "totals": run.totals,
                      "results": res}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
