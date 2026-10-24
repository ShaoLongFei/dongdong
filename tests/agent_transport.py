"""OpenAI 兼容流式传输的边界测试，和 loop 测试分文件保持 ≤300 行。"""

import asyncio
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "device-mac"))

from dongdong import llm  # noqa: E402
from dongdong.llm import ModelConfig  # noqa: E402


def test_stream_chat_transport_roundtrip():
    async def run():
        requests: list[dict] = []
        responses = [
            "data: "
            + json.dumps(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {
                                            "name": "read_file",
                                            "arguments": '{"path":"a.txt"}',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            )
            + "\n\ndata: [DONE]\n\n",
            "data: "
            + json.dumps({"choices": [{"delta": {"content": "done"}}]})
            + "\n\ndata: [DONE]\n\n",
        ]

        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=responses[len(requests) - 1].encode(),
            )

        transport = httpx.MockTransport(handler)
        cfg = ModelConfig(
            "http://fake",
            "k",
            "deepseek-flash",
            extra_body={"thinking": {"type": "disabled"}},
        )
        tools = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]
        messages = [{"role": "user", "content": "读一下"}]
        first = [e async for e in llm.stream_chat(cfg, messages, tools, transport=transport)]
        call = first[-1].tool_calls[0]
        assert call.name == "read_file"
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": call.id, "content": "ok"})
        second = [e async for e in llm.stream_chat(cfg, messages, tools, transport=transport)]
        assert second[-1].text == "done"
        assert requests[0]["thinking"] == {"type": "disabled"}
        assert requests[0]["model"] == "deepseek-flash"
        assert requests[1]["messages"][-1]["role"] == "tool"
        assert requests[1]["messages"][-1]["content"] == "ok"
        assert requests[1]["tools"] == tools

    asyncio.run(run())


def test_stream_chat_http_error():
    async def run():
        def handler(request):
            return httpx.Response(400, content=b'{"error":"bad"}')

        cfg = ModelConfig("http://fake", "k", "m")
        try:
            async for _ in llm.stream_chat(cfg, [], [], transport=httpx.MockTransport(handler)):
                pass
        except RuntimeError as exc:
            assert "HTTP 400" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")

    asyncio.run(run())


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("agent transport 通过" if not failures else f"{failures} 个失败")
    sys.exit(1 if failures else 0)
