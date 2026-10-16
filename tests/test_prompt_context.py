import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from core.prompt_context import (MEMORY_BEGIN, MEMORY_END, fit_system_prompt,
                                 replace_memory_context, prompt_byte_limit)

class Settings:
    def __init__(self, limit=2000): self.limit=limit
    def get(self, key, default=None):
        return {"core.api-provider":"openrouter", "core.openrouter.model":"fixture/model",
                "core.provider_context_limits":{"openrouter/fixture/model":{
                    "context_tokens":self.limit,"output_reserve_tokens":0,
                    "history_reserve_tokens":0,"framing_reserve_tokens":0}}}.get(key,default)

def prompt(memory):
    return "PROTECTED IDENTITY\n" + MEMORY_BEGIN + memory + MEMORY_END + "\nPROTECTED MANUAL"

def test_provider_limit_protects_identity_and_manual():
    settings=Settings(220)
    text=fit_system_prompt(prompt("é"*200),settings)
    assert len(text.encode("utf-8"))<=prompt_byte_limit(settings)
    assert "PROTECTED IDENTITY" in text and "PROTECTED MANUAL" in text
    assert "truncated" in text
    with pytest.raises(ValueError):fit_system_prompt(prompt("hello"),Settings(20))

def test_tool_schema_counts_against_provider_budget():
    with pytest.raises(ValueError):prompt_byte_limit(Settings(100),[{"description":"x"*200}])

def test_replace_preserves_other_memory_and_removes_old_prospective():
    old=prompt("\nmirrors and continuity\n--- Morphological Memory (Prospective Attractors [Rev: 1]) ---\nold drawer")
    new=replace_memory_context(old,"[Rev: 2] new drawer")
    assert "old drawer" not in new and "new drawer" in new
    assert "mirrors and continuity" in new
    assert replace_memory_context(new,"[Rev: 3] latest").count("Prospective rule:")==1

@pytest.mark.parametrize("module,class_name",[("openrouter_worker","OpenRouterWorker"),
    ("chatgpt_worker","ChatGptWorker"),("claude_worker","ClaudeWorker"),
    ("deepseek_worker","DeepSeekWorker"),("agy_worker","AgyWorker")])
def test_actual_worker_adapter_keeps_history(module,class_name):
    import importlib
    cls=getattr(importlib.import_module("core."+module),class_name)
    worker=cls.__new__(cls);worker.settings=Settings();worker.tools=[]
    worker.history=[{"role":"tool","content":"[Rev: 1] historical response"}]
    worker.sys_instruct=prompt("--- Morphological Memory (Prospective Attractors [Rev: 1]) ---\nold")
    worker.update_system_instruction(replace_memory_context(worker.sys_instruct,"[Rev: 2] fresh drawer"))
    assert "fresh drawer" in worker.sys_instruct and "old" not in worker.sys_instruct
    assert len(worker.history)==1

def test_gemini_adapter_recreates_chat_with_history():
    from core.gemini_worker import GeminiWorker
    from google.genai import types
    worker=GeminiWorker.__new__(GeminiWorker);worker.settings=Settings();worker.tools=[]
    worker.thinker_chat=object();worker.thinker_config=types.GenerateContentConfig(system_instruction=prompt("old"))
    worker.current_thinker_model="fixture";worker.client=MagicMock()
    history=["paired calls preserved"];worker._get_chat_history=lambda:history
    worker.update_system_instruction(prompt("fresh"))
    assert worker.client.chats.create.call_args.kwargs["history"]==history
    assert "fresh" in worker.thinker_config.system_instruction

def test_real_tool_orchestrator_worker_refresh_round_trip():
    from core.orchestrator import AmityOrchestrator as Orchestrator
    from core.openrouter_worker import OpenRouterWorker
    from tools.mempalace_tool import MemPalaceTool
    orch=Orchestrator.__new__(Orchestrator);orch.settings_manager=Settings(4000)
    orch.system_prompt=prompt("mirrors\n--- Morphological Memory (Prospective Attractors [Rev: 1]) ---\nold drawer")
    orch.build_system_prompt=lambda:None
    orch.cerebrum=SimpleNamespace(get_all_tool_declarations=lambda:[])
    worker=OpenRouterWorker.__new__(OpenRouterWorker);worker.settings=orch.settings_manager;worker.tools=[]
    worker.history=[{"role":"tool","content":"[Rev: 1] old drawer"}];worker.sys_instruct=orch.system_prompt
    orch.gemini_worker=worker;orch.agent_id="fixture"
    orch.mempalace_manager=SimpleNamespace(refresh_prospective_anchors=lambda:"[Notice: Supersedes prior Rev: 1]\n[Rev: 2] fresh drawer")
    tool=MemPalaceTool.__new__(MemPalaceTool);tool.orchestrator=orch;tool.manager=orch.mempalace_manager
    result=tool.execute("refresh_prospective_anchors",{})
    assert "fresh drawer" in result and "fresh drawer" in worker.sys_instruct
    assert "old drawer" not in worker.sys_instruct
    assert "mirrors" in worker.sys_instruct
    assert len(worker.history)==1
