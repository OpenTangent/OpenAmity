"""Provider-specific system prompt budgets and prospective-context replacement.

UTF-8 bytes are deliberately used as a conservative text-token bound rather
than a chars/4 guess. The configured context limit is an operator contract,
not claimed automatic discovery. History/tool/output reserves are explicit.
"""
import json
import re

MEMORY_BEGIN = "[OPENAMITY_MEMORY_BEGIN]"
MEMORY_END = "[OPENAMITY_MEMORY_END]"


def selected_provider(settings):
    if settings.get("core.antigravity.agy-mode", False):
        return "antigravity"
    provider = settings.get("core.api-provider", "gemini")
    return "chatgpt" if provider == "openai" else provider


def prompt_byte_limit(settings, tool_declarations=None):
    provider = selected_provider(settings)
    low = settings.get("core.low-token-mode", False)
    model = settings.get(f"core.{provider}.{'light-model' if low else 'model'}", "")
    limits = settings.get("core.provider_context_limits", {}) or {}
    entry = limits.get(f"{provider}/{model}", limits.get(provider, limits.get("default", {})))
    if not isinstance(entry, dict):
        raise ValueError("Provider context budget must be an object")
    def positive(name, default, zero=False):
        value = entry.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < (0 if zero else 1):
            raise ValueError(f"Invalid provider budget {name}")
        return value
    context = positive("context_tokens", 131072)
    output = positive("output_reserve_tokens", 4096, True)
    history = positive("history_reserve_tokens", 8192, True)
    framing = positive("framing_reserve_tokens", 1024, True)
    tools = len(json.dumps(tool_declarations or [], ensure_ascii=False).encode("utf-8"))
    remaining = context - output - history - framing - tools
    if remaining < 1:
        raise ValueError(f"No system prompt budget remains for {provider}/{model}")
    return remaining


def fit_system_prompt(text, settings, tools=None):
    limit = prompt_byte_limit(settings, tools)
    if len(text.encode("utf-8")) <= limit:
        return text
    start = text.find(MEMORY_BEGIN)
    end = text.find(MEMORY_END, start)
    if start < 0 or end < 0:
        raise ValueError("Protected system instructions exceed provider budget")
    before = text[:start + len(MEMORY_BEGIN)]
    after = text[end:]
    notice = "\n[Notice: memory context truncated to configured provider budget]\n"
    space = limit - len((before + notice + after).encode("utf-8"))
    if space < 0:
        raise ValueError("Identity/manual exceed provider system budget; refusing truncation")
    dynamic = text[start + len(MEMORY_BEGIN):end].encode("utf-8")[:space].decode("utf-8", errors="ignore")
    return before + dynamic + notice + after


def replace_memory_context(prompt, context):
    # Replace the one dynamic memory block, preserving identity and manual.
    start = prompt.find(MEMORY_BEGIN)
    end = prompt.find(MEMORY_END, start)
    if start < 0 or end < 0:
        raise ValueError("System prompt has no replaceable memory block")
    dynamic = prompt[start + len(MEMORY_BEGIN):end]
    # Prospective anchors are the final dynamic section. Keep mirrors/ST.
    marker = dynamic.find("--- Morphological Memory (Prospective Attractors")
    if marker >= 0:
        dynamic = dynamic[:marker]
    dynamic = re.sub(r"\n\[Prospective rule:[^\n]*\]\n.*", "", dynamic, flags=re.S)
    rule = "\n[Prospective rule: the latest system memory block replaces all lower-revision prospective tool/history context.]\n"
    return prompt[:start + len(MEMORY_BEGIN)] + dynamic + rule + context + "\n" + prompt[end:]



class MemoryContextWorkerMixin:
    def update_system_instruction(self, system_instruction):
        """Update system policy without dropping tool-call/result history."""
        text = fit_system_prompt(system_instruction, self.settings, getattr(self, "tools", None))
        # Gemini chat config is captured at creation; recreate with unchanged
        # history so the next function response uses the updated system block.
        if getattr(self, "thinker_chat", None) is not None:
            config = self.thinker_config.model_copy(update={"system_instruction": text})
            history = self._get_chat_history()
            chat = self.client.chats.create(model=self.current_thinker_model, config=config, history=history)
            self.thinker_config = config
            self.thinker_chat = chat
        self.sys_instruct = text
