"""Unit checks for the dashboard analyst service's pure helpers: rich-block
parsing (chart/table/choices) and server-side scope injection. The LLM loop
itself needs Azure + Cube, so it is exercised via the live smoke suite, not here
— except the reason/act/observe plumbing (parallel tool execution + ordering),
which is checked below with a stubbed router and tool runner.
"""

import asyncio
import json
import time

from seleric_mcp.gateway import analyst as A


def test_parse_rich_blocks_strips_chart_and_table():
    raw = (
        "Net revenue up.\n\n"
        "```chart\n"
        '{"type":"line","x":"date","series":[{"key":"nr","label":"NR"}],'
        '"rows":[{"date":"2026-07-01","nr":100}]}\n'
        "```\n\n"
        "```table\n"
        '{"columns":[{"key":"c","label":"Ch"},{"key":"nr","format":"inr","align":"right"}],'
        '"rows":[{"c":"ig","nr":100}],"primary":"nr"}\n'
        "```\n"
        "Period: last 7d"
    )
    out = A.parse_rich_blocks(raw)
    assert out["chart"]["type"] == "line"
    assert out["table"]["primary"] == "nr"
    assert "```" not in out["answer"]
    assert "Net revenue up." in out["answer"]


def test_malformed_block_is_stripped_not_rendered():
    raw = "Answer.\n```table\n{not valid json}\n```"
    out = A.parse_rich_blocks(raw)
    assert "table" not in out
    assert "```" not in out["answer"]


def test_inject_scope_forces_module_and_brand_filter():
    a = A.inject_scope("metrics_query", {"measures": ["x"]}, module="commerce", brand_id="20")
    assert a["module"] == "commerce"
    assert any(f["dimension"] == "brand_id" for f in a["filters"])

    d = A.inject_scope("metrics_drilldown", {}, module="commerce", brand_id="20")
    assert any(f["dimension"] == "brand_id" for f in d["additional_filters"])

    # brand filter is not duplicated when already present
    a2 = A.inject_scope(
        "metrics_query",
        {"measures": ["x"], "filters": [{"dimension": "brand_id", "operator": "equals", "values": ["7"]}]},
        module="commerce",
        brand_id="20",
    )
    assert sum(1 for f in a2["filters"] if f["dimension"] == "brand_id") == 1


def test_flag_repeated_failure_only_hints_after_second_identical_failure():
    fail_counts: dict[str, int] = {}
    payload = '{"error": "no ad platform connected"}'

    # First failure: passed through untouched.
    first = A._flag_repeated_failure(payload, fail_counts, "metrics_query", {"m": "total_ad_spend"})
    assert "hint" not in first

    # Second identical failure: gets a hint telling the model to stop retrying.
    second = A._flag_repeated_failure(payload, fail_counts, "metrics_query", {"m": "total_ad_spend"})
    assert "do not retry" in second.lower()

    # A different call (different args) is tracked independently and isn't hinted yet.
    other = A._flag_repeated_failure(payload, fail_counts, "metrics_query", {"m": "revenue"})
    assert "hint" not in other


def test_choices_capped_and_cleaned():
    _, choices = A._extract_choices(
        'Pick.\n```choices\n{"options":[{"label":"A","value":"do A"},{"label":"B"}]}\n```'
    )
    assert choices == [{"label": "A", "value": "do A"}, {"label": "B", "value": "B"}]


# --- reason -> act -> observe loop: parallel tool execution + ordering --------


class _FakeFn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.type = "function"
        self.function = _FakeFn(name, arguments)


class _FakeMessage:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeResp:
    def __init__(self, message):
        self.message = message
        self.model = "fake-model"


class _FakeRouter:
    """Replays a scripted list of messages, one per complete() call. The final
    synthesis pass calls complete_stream(), which yields the answer in pieces."""

    def __init__(self, scripted, stream_pieces=("All ", "done.")):
        self._scripted = scripted
        self._stream_pieces = stream_pieces
        self.calls = 0

    def complete(self, *, tier, messages, tools, tool_choice):  # sync, run via to_thread
        msg = self._scripted[min(self.calls, len(self._scripted) - 1)]
        self.calls += 1
        return _FakeResp(msg)

    def complete_stream(self, *, tier, messages):  # sync generator of text deltas
        yield from self._stream_pieces


def _drive(svc, session):
    events = []

    async def go():
        async for ev in svc._run_turn(
            session, session_id="s1", question="q", module=None, brand_id=None
        ):
            events.append(ev)

    asyncio.run(go())
    return events


def test_act_phase_runs_tools_in_parallel_and_observes_in_order():
    # Round 1: model asks for TWO tools at once. Round 2: no tools -> the final
    # synthesis streams via complete_stream ("All " + "done.").
    two_calls = _FakeMessage(
        tool_calls=[
            _FakeToolCall("c1", "metrics_query", json.dumps({"measures": ["a"]})),
            _FakeToolCall("c2", "metrics_query", json.dumps({"measures": ["b"]})),
        ]
    )
    router = _FakeRouter([two_calls, _FakeMessage()])

    svc = object.__new__(A.AnalystService)  # skip __init__ (no Azure/Cube needed)
    svc._router = router
    svc.max_rounds = 40
    svc._tools = []  # _openai_tools returns this without touching _mcp

    intervals = []  # (start, end) of each tool run, in completion order

    async def fake_run_tool(name, args, session):
        start = time.monotonic()
        await asyncio.sleep(0.1)  # simulate a Cube round-trip
        end = time.monotonic()
        intervals.append((start, end))
        return json.dumps({"measures": args.get("measures")})

    svc._run_tool = fake_run_tool

    session = A.AnalystSession("system", None, None)
    events = _drive(svc, session)

    # Two tool runs overlapped in time -> they ran concurrently, not in series.
    assert len(intervals) == 2
    (s0, e0), (s1, e1) = intervals
    assert s0 < e1 and s1 < e0, "tool runs did not overlap (still sequential)"

    # Steps are observed back in call order (a before b), and one final answer.
    steps = [ev for ev in events if ev["type"] == "step"]
    finals = [ev for ev in events if ev["type"] == "final"]
    assert [st["label"] for st in steps] == ["metrics_query: a", "metrics_query: b"]
    assert len(finals) == 1 and finals[0]["answer"] == "All done."
    # The answer streamed as token deltas that concatenate to the final answer.
    tokens = [ev["delta"] for ev in events if ev["type"] == "token"]
    assert "".join(tokens) == "All done."
    # Both tool results are in history, one per tool_call id.
    tool_ids = {m["tool_call_id"] for m in session.messages if m.get("role") == "tool"}
    assert tool_ids == {"c1", "c2"}


def test_fence_stripper_hides_fenced_blocks_streamed_across_chunks():
    fs = A._FenceStripper()
    # ``` markers deliberately split across chunk boundaries.
    chunks = ["Sales up 3%. ", "``", "`table\n{\"rows\":[]}", "\n``", "`", " Ask more?"]
    out = "".join(fs.feed(c) for c in chunks) + fs.flush()
    assert "table" not in out and "{" not in out  # fenced JSON never leaked
    assert "Sales up 3%." in out and "Ask more?" in out  # prose survives
