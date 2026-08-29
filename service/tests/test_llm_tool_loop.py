from __future__ import annotations

import asyncio
import base64
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


@pytest.fixture()
def temp_db(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        monkeypatch.setenv("DESKBOT_DB_PATH", str(db_path))
        from deskbot_server.db import init_database
        from deskbot_server.db.engine import init_engine, reset_engine

        reset_engine()
        init_engine(db_path)
        init_database()
        yield db_path


def test_complete_llm_with_tool_loop_two_rounds(temp_db):
    from deskbot_server.service.application.llm_tool_loop import complete_llm_with_tool_loop

    round1 = json.dumps(
        {"tts": "", "tools": [{"tool": "memory_add", "text": "喜欢猫"}], "moves": [], "anims": []}, ensure_ascii=False
    )
    round2 = json.dumps({"tts": "已记住你喜欢猫", "tools": [], "moves": [], "anims": []}, ensure_ascii=False)

    chat = AsyncMock()
    chat.llm = AsyncMock(side_effect=[round1, round2])

    async def _run():
        return await complete_llm_with_tool_loop(chat, "记住我喜欢猫", device_id="deskbot_a", request_id="req1")

    parsed, tools, results, raw = asyncio.run(_run())
    assert parsed["reply"] == "已记住你喜欢猫"
    assert len(tools) == 1
    assert tools[0]["tool"] == "memory_add"
    assert len(results) == 1
    assert results[0]["ok"] is True
    assert chat.llm.call_count == 2
    assert raw == round2


def test_complete_llm_with_tool_loop_single_round():
    from deskbot_server.service.application.llm_tool_loop import complete_llm_with_tool_loop

    answer = json.dumps({"tts": "你好", "tools": [], "moves": [], "anims": []})

    class _FakeChat:
        async def llm(
            self,
            text,
            *,
            device_context=None,
            device_id=None,
            history_messages=None,
            extra_messages=None,
            on_tts_ready=None,
        ):
            return answer

    async def _run():
        return await complete_llm_with_tool_loop(_FakeChat(), "你好", device_id="deskbot_a")

    parsed, tools, results, _raw = asyncio.run(_run())
    assert parsed["reply"] == "你好"
    assert tools == []
    assert results == []


def test_camera_tool_prompt_delegates_visual_decision_to_llm():
    from deskbot_server.infrastructure.llm.utils import llm_tools_prompt_appendix

    prompt = llm_tools_prompt_appendix()
    assert "由你结合用户意图、上下文和已有工具结果自主判断" in prompt
    assert "ASR 轻微错字不应妨碍判断" in prompt
    assert "视觉对话中的追问若依赖当前画面，应重新拍摄" in prompt
    assert "不确定是否需要当前画面时，也优先调用相机" in prompt
    assert "禁止要求主人把物品移近、举高或换位置" in prompt


def test_llm_scheduled_camera_tool_attaches_jpeg_to_followup_round(monkeypatch):
    from deskbot_server.service.application import llm_tool_loop

    jpeg_b64 = base64.b64encode(b"\xff\xd8camera\xff\xd9").decode("ascii")

    async def fake_execute(*_args, **_kwargs):
        return [
            {
                "tool": "capture_camera",
                "ok": True,
                "width": 320,
                "height": 240,
                "jpeg_bytes": 12,
                "jpeg_base64": jpeg_b64,
            }
        ]

    monkeypatch.setattr(llm_tool_loop, "_execute_tools_round", fake_execute)
    tool_call = json.dumps(
        {"tts": "我再看一下", "tools": [{"tool": "capture_camera"}], "moves": [], "anims": []},
        ensure_ascii=False,
    )
    final = json.dumps({"tts": "这是一个测试物品", "tools": [], "moves": [], "anims": []}, ensure_ascii=False)
    chat = AsyncMock()
    chat.llm = AsyncMock(side_effect=[tool_call, final])

    async def _run():
        return await llm_tool_loop.complete_llm_with_tool_loop(
            chat,
            "再确认下刚才那个",
            device_id="deskbot_camera",
            request_id="vision-1",
        )

    parsed, tools, results, _raw = asyncio.run(_run())
    assert parsed["reply"] == "这是一个测试物品"
    assert tools == [{"tool": "capture_camera"}]
    assert results[0]["ok"] is True
    assert chat.llm.call_count == 2
    assert chat.llm.call_args_list[0].kwargs["extra_messages"] is None

    extra_messages = chat.llm.call_args_list[1].kwargs["extra_messages"]
    assert extra_messages[0] == {"role": "assistant", "content": tool_call}
    content = extra_messages[1]["content"]
    assert content[0]["type"] == "input_text"
    assert "直接观察图片" in content[0]["text"]
    assert content[1] == {
        "type": "input_image",
        "image_url": f"data:image/jpeg;base64,{jpeg_b64}",
    }
