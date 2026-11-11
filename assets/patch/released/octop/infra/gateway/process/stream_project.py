"""Project harness stream chunks into octop-gateway MessageEvent objects."""

from __future__ import annotations

from contextlib import aclosing as _octop_owned_stream

from collections.abc import AsyncIterator
import json
from pathlib import PurePosixPath
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from octop_gateway.media import MediaBackend
from octop_gateway.models import MessageEvent

from octop.i18n import channel_tool_hint_end, channel_tool_hint_start, tool_display_name
from octop.infra.gateway.hitl.format import format_hitl_card, normalize_hitl_request
from octop.infra.gateway.hitl.stream_compat import install_harness_hitl_compat
from octop.infra.gateway.media.tool_media import (
    dedup_tool_result_messages,
    media_events_from_tool_result,
)
from octop.infra.gateway.process.agent_resolve import harness_workspace_for_agent
from octop.infra.gateway.process.usage_record import UsageTracker
from octop.infra.history.projection import TurnHistoryTracker
from octop.infra.utils.locale import DEFAULT_LOCALE, Locale, normalize_locale
from octop.infra.utils.turn_failure import log_failed_tool_results

if TYPE_CHECKING:
    from octop.infra.agents.manager import AgentManager
    from octop.infra.gateway.hitl.coordinator import HitlChannelCoordinator, HitlStreamContext

install_harness_hitl_compat()


@dataclass
class StreamProjectionState:
    hitl_paused: bool = False
    hitl_pending_id: str | None = None


@dataclass
class _ToolProjectionState:
    current_node: str | None = None
    tool_name_buf: dict[str, str] = field(default_factory=dict)
    tool_started: set[str] = field(default_factory=set)
    tool_ids: dict[str, str] = field(default_factory=dict)
    tool_args: dict[str, str] = field(default_factory=dict)
    active_tool_idx: str | None = None
    # True once we've seen at least one tool_call_chunk in this stream.
    # Media events are only emitted when a tool was actually called this turn;
    # historical tool results (replayed via PatchToolCallsMiddleware / Overwrite)
    # must not trigger re-sends.
    saw_tool_call: bool = False
    # Track tool_call_ids whose media we've already emitted to prevent
    # re-emission when PatchToolCallsMiddleware emits Overwrite(full_history).
    emitted_media_ids: set[str] = field(default_factory=set)
    emitted_tool_error_ids: set[str] = field(default_factory=set)


def _tool_input_summary(raw_name: str, arguments: str) -> str:
    """A basename for file reads; shell arguments and full paths stay private."""
    if "read_file" not in raw_name.lower() and "读取文件" not in raw_name:
        return ""
    try:
        data = json.loads(arguments)
    except (TypeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    for key in ("file_path", "path", "filename", "file"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            name = PurePosixPath(value.replace(chr(92), "/")).name
            return " ".join(name.split())[:120]
    return ""


def enrich_tool_stream_chunk(
    chunk: dict[str, Any],
    locale: str | Locale,
    *,
    agent_manager: AgentManager | None = None,
    agent_id: str | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Add ``display_name`` to harness tool stream chunks for dashboard clients."""
    if chunk.get("type") not in ("tool_call_chunk", "tool_result"):
        return chunk
    name = chunk.get("name")
    if not isinstance(name, str) or not name:
        return chunk
    out = dict(chunk)
    if agent_manager is not None and agent_id:
        out["display_name"] = agent_manager.resolve_tool_display_name_for_chat(
            agent_id=agent_id,
            user_id=user_id,
            tool_name=name,
            locale=str(locale),
        )
    else:
        out["display_name"] = tool_display_name(name, locale)
    return out


async def _project_chunks(
    chunks: AsyncIterator[dict[str, Any]],
    *,
    agent_manager: AgentManager,
    agent_id: str,
    locale: str | Locale,
    usage_tracker: UsageTracker | None,
    history_tracker: TurnHistoryTracker | None,
    projection_state: StreamProjectionState | None,
    hitl_coordinator: HitlChannelCoordinator | None,
    hitl_ctx: HitlStreamContext | None,
) -> AsyncIterator[MessageEvent]:
    loc = normalize_locale(str(locale))
    tool_state = _ToolProjectionState()
    harness_workspace = harness_workspace_for_agent(agent_manager, agent_id)

    def _tool_label(raw: str) -> str:
        return tool_display_name(raw, loc)

    def _tool_start(raw: str, idx: str) -> MessageEvent:
        label = _tool_label(raw)
        return MessageEvent.tool_start(
            label,
            tool_hint_text=channel_tool_hint_start(label, loc),
            # Raw tool id for invoke-collapse / team channel UX (label is localized).
            tool_key=raw,
            tool_call_id=tool_state.tool_ids.get(idx, ""),
            tool_input_summary=_tool_input_summary(raw, tool_state.tool_args.get(idx, "")),
        )

    def _tool_end(raw: str, idx: str, *, is_error: bool = False) -> MessageEvent:
        label = _tool_label(raw)
        return MessageEvent.tool_end(
            label,
            tool_hint_text=channel_tool_hint_end(label, loc),
            tool_key=raw,
            tool_call_id=tool_state.tool_ids.get(idx, ""),
            tool_input_summary=_tool_input_summary(raw, tool_state.tool_args.get(idx, "")),
            is_error=is_error,
        )

    async with _octop_owned_stream(chunks) as _owned_events_146:
        async for chunk in _owned_events_146:
            if usage_tracker is not None:
                usage_tracker.observe(chunk)
            if history_tracker is not None:
                history_tracker.observe(chunk)
                from octop.infra.history.recorder import flush_tracker  # noqa: PLC0415

                await flush_tracker(history_tracker)
            ctype: str = chunk.get("type", "")
            node: str | None = chunk.get("node")

            if ctype == "token":
                if tool_state.current_node is not None and node != tool_state.current_node:
                    yield MessageEvent.flush()
                tool_state.current_node = node
                content = chunk.get("content") or ""
                if content:
                    yield MessageEvent.delta(content)

            elif ctype == "reasoning":
                content = chunk.get("content") or ""
                if content:
                    yield MessageEvent.thinking_delta(content)

            elif ctype == "tool_call_chunk":
                tool_state.saw_tool_call = True
                idx_key = f"_idx_{chunk.get('index', 0)}"
                tool_state.active_tool_idx = idx_key

                name_frag: str = chunk.get("name") or ""
                args_frag: str = chunk.get("args") or ""
                if idx_key not in tool_state.tool_ids:
                    tool_state.tool_ids[idx_key] = str(chunk.get("id") or uuid.uuid4().hex)
                if isinstance(args_frag, str):
                    tool_state.tool_args[idx_key] = tool_state.tool_args.get(idx_key, "") + args_frag

                if name_frag:
                    tool_state.tool_name_buf[idx_key] = (
                        tool_state.tool_name_buf.get(idx_key, "") + name_frag
                    )

                if (
                    args_frag
                    and idx_key not in tool_state.tool_started
                    and tool_state.tool_name_buf.get(idx_key)
                ):
                    tool_state.tool_started.add(idx_key)
                    yield _tool_start(tool_state.tool_name_buf[idx_key], idx_key)

            elif ctype == "tool_result":
                log_failed_tool_results(
                    chunk,
                    agent_id=agent_id,
                    thread_id=(hitl_ctx.thread_id if hitl_ctx is not None else ""),
                    seen=tool_state.emitted_tool_error_ids,
                    live=tool_state.saw_tool_call,
                )
                # Results may contain several parallel tools and replayed history.
                # Match actual call IDs, rather than finishing only the last index.
                matched: list[tuple[str, bool]] = []
                tool_messages = []
                for result in chunk.get("messages") or []:
                    field_value = result.get if isinstance(result, dict) else lambda k, default=None: getattr(result, k, default)
                    if field_value("type") != "tool" and field_value("role") != "tool":
                        continue
                    tool_messages.append(result)
                    result_id = str(field_value("tool_call_id") or "")
                    result_name = str(field_value("name") or "")
                    indices = [idx for idx, call_id in tool_state.tool_ids.items() if result_id and call_id == result_id]
                    if not indices and not result_id and result_name:
                        indices = [idx for idx, name in tool_state.tool_name_buf.items() if name == result_name]
                    if indices:
                        matched.append((indices[0], field_value("status") == "error"))
                if not tool_messages and tool_state.active_tool_idx is not None:
                    matched.append((tool_state.active_tool_idx, False))
                for idx, is_error in matched:
                    if idx not in tool_state.tool_ids:
                        continue
                    final_name = tool_state.tool_name_buf.get(idx) or "tool"
                    if idx not in tool_state.tool_started:
                        tool_state.tool_started.add(idx)
                        yield _tool_start(final_name, idx)
                    yield _tool_end(final_name, idx, is_error=is_error)
                    tool_state.tool_name_buf.pop(idx, None)
                    tool_state.tool_args.pop(idx, None)
                    tool_state.tool_ids.pop(idx, None)
                    tool_state.tool_started.discard(idx)
                    if tool_state.active_tool_idx == idx:
                        tool_state.active_tool_idx = None
                if harness_workspace is not None and tool_state.saw_tool_call:
                    chunk_for_media = dedup_tool_result_messages(chunk, tool_state.emitted_media_ids)
                    if chunk_for_media is not None:
                        async for media_event in media_events_from_tool_result(
                            chunk_for_media,
                            workspace=harness_workspace,
                        ):
                            yield media_event
                tool_state.current_node = node

            elif ctype == "hitl_required":
                request = chunk.get("request")
                if not isinstance(request, dict):
                    request = {}
                request = normalize_hitl_request(request)
                if hitl_coordinator is not None and hitl_ctx is not None:
                    record = hitl_coordinator.register_from_request(request, ctx=hitl_ctx)
                    request["pending_id"] = record.pending_id
                    card = format_hitl_card(
                        record.action_requests,
                        pending_id=record.pending_id,
                        locale=loc,
                    )
                    if projection_state is not None:
                        projection_state.hitl_paused = True
                        projection_state.hitl_pending_id = record.pending_id
                    yield MessageEvent.text(card)
                return

            elif ctype == "state_update":
                if tool_state.current_node is not None and node != tool_state.current_node:
                    yield MessageEvent.flush()
                tool_state.current_node = node


async def project_stream(
    agent_manager: AgentManager,
    agent_id: str,
    request: dict[str, Any],
    *,
    media_backend: MediaBackend | None = None,
    usage_tracker: UsageTracker | None = None,
    history_tracker: TurnHistoryTracker | None = None,
    locale: str | Locale = DEFAULT_LOCALE,
    projection_state: StreamProjectionState | None = None,
    hitl_coordinator: HitlChannelCoordinator | None = None,
    hitl_ctx: HitlStreamContext | None = None,
) -> AsyncIterator[MessageEvent]:
    del media_backend  # IM tool media uses agent.backend directly
    async with _octop_owned_stream(_project_chunks(
        agent_manager.stream(agent_id, request),
        agent_manager=agent_manager,
        agent_id=agent_id,
        locale=locale,
        usage_tracker=usage_tracker,
        history_tracker=history_tracker,
        projection_state=projection_state,
        hitl_coordinator=hitl_coordinator,
        hitl_ctx=hitl_ctx,
    )) as _owned_events_284:
        async for ev in _owned_events_284:
            yield ev


async def project_resume_stream(
    agent_manager: AgentManager,
    agent_id: str,
    thread_id: str,
    decisions: list[dict[str, Any]],
    *,
    usage_tracker: UsageTracker | None = None,
    history_tracker: TurnHistoryTracker | None = None,
    locale: str | Locale = DEFAULT_LOCALE,
    projection_state: StreamProjectionState | None = None,
    hitl_coordinator: HitlChannelCoordinator | None = None,
    hitl_ctx: HitlStreamContext | None = None,
) -> AsyncIterator[MessageEvent]:
    async with _octop_owned_stream(_project_chunks(
        agent_manager.resume_hitl(agent_id, thread_id, decisions),
        agent_manager=agent_manager,
        agent_id=agent_id,
        locale=locale,
        usage_tracker=usage_tracker,
        history_tracker=history_tracker,
        projection_state=projection_state,
        hitl_coordinator=hitl_coordinator,
        hitl_ctx=hitl_ctx,
    )) as _owned_events_311:
        async for ev in _owned_events_311:
            yield ev
