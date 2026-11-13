"""Provider-specific system prompt budgets and prospective-context replacement.

UTF-8 bytes are deliberately used as a conservative text-token bound rather
than a chars/4 guess. The configured context limit is an operator contract,
not claimed automatic discovery. History/tool/output reserves are explicit.
"""
import json
import re

MEMORY_BEGIN = "[OPENAMITY_MEMORY_BEGIN]"
MEMORY_END = "[OPENAMITY_MEMORY_END]"

WORKER_CRITICAL_INSTRUCTION = (
    "CRITICAL INSTRUCTION: Output your internal reasoning as plain text BEFORE invoking any tool(s), "
    "and emit native tool call(s) in the EXACT SAME TURN—never end a turn with reasoning alone when acting."
)
WORKER_AUTONOMOUS_SPEECH_INSTRUCTION = (
    "AUTONOMOUS SPEECH INSTRUCTION: You are fully autonomous regarding your speech and will NOT speak automatically. "
    "To reply to the user on the GUI, you MUST explicitly call Speaker_speak_aloud (or Speaker_output_text strictly for non-speakable text) "
    "using a first-person perspective—otherwise you remain completely silent."
)
WORKER_STATIC_INSTRUCTIONS = (
    f"{WORKER_CRITICAL_INSTRUCTION}\n\n{WORKER_AUTONOMOUS_SPEECH_INSTRUCTION}"
)


def inject_static_worker_instructions(system_instruction: str, extra: str = "") -> str:
    """Insert static worker directives before MEMORY_BEGIN so dynamic memory stays trailing."""
    base = system_instruction or ""
    directive = extra or WORKER_STATIC_INSTRUCTIONS
    if "CRITICAL INSTRUCTION:" in base and "AUTONOMOUS SPEECH INSTRUCTION:" in base:
        return base
    idx = base.find(MEMORY_BEGIN)
    if idx >= 0:
        prefix = base[:idx].rstrip()
        suffix = base[idx:]
        return f"{prefix}\n\n{directive}\n\n{suffix}" if prefix else f"{directive}\n\n{suffix}"
    return f"{base}\n\n{directive}" if base else directive


def split_system_prompt_for_caching(system_instruction: str):
    """Split prompt into (static_prefix, dynamic_memory_block) at MEMORY_BEGIN."""
    text = system_instruction or ""
    idx = text.find(MEMORY_BEGIN)
    if idx <= 0:
        return text, ""
    return text[:idx].rstrip("\n"), text[idx:]


def format_prompt_footprint(system_instruction: str, tool_declarations=None) -> str:
    """Summarize UTF-8 bytes and estimated tokens for static prefix, dynamic block, and tools."""
    static_part, dynamic_part = split_system_prompt_for_caching(system_instruction or "")
    static_b = len(static_part.encode("utf-8"))
    dynamic_b = len(dynamic_part.encode("utf-8"))
    tools_b = len(json.dumps(tool_declarations or [], ensure_ascii=False).encode("utf-8"))
    total_b = static_b + dynamic_b + tools_b
    return (
        f"Static={static_b}B (~{len(static_part)//4} tok) | "
        f"Dynamic={dynamic_b}B (~{len(dynamic_part)//4} tok) | "
        f"Tools={tools_b}B (~{tools_b//4} tok) | "
        f"Total={total_b}B (~{total_b//4} tok)"
    )


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
        if getattr(self, "_append_worker_static_instructions", False):
            system_instruction = inject_static_worker_instructions(system_instruction or "")
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
