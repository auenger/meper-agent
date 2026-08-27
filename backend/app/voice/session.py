"""VoiceSession — per-connection realtime voice state machine.

Stage 4 (current): ASR (stage 2) + harness brain (stage 3) + streaming TTS.
The brain's ``text_delta`` is sentence-buffered and pumped to TTS on a
dedicated task so harness streaming is never blocked waiting on synthesis.
Barge-in (``_handle_interrupt``) is implemented but not yet auto-triggered —
stage 5 wires VAD ``speech_start`` to call it.

Design notes:
- ``_turn_lock`` serializes turns per session (a new utterance waits for the
  previous turn's brain + TTS to finish or be aborted).
- ``_active_llm_task`` / ``turn.tts_task`` are kept as handles for barge-in.
- The fire-and-forget ``AgentExecutionService.stream()`` wrapper is bypassed:
  we call harness ``stream()`` directly to hold a cancellable task.
- TTS client is created lazily once per session and reused across turns
  (Volcano WS connection stays warm).
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import math
import re
import struct
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket
from loguru import logger

if TYPE_CHECKING:
    from app.core.auth_apikey import ApiKeyPrincipal

from app.voice import protocol as P  # noqa: N812
from app.voice.config import VoiceRuntimeConfig
from app.voice.providers.base import STTProvider, TTSProvider
from app.voice.turn import TurnContext
from app.voice.vad import Speech, create_vad

# Sentence-ending punctuation — flush a TTS chunk when one is seen, to cut
# first-packet latency (don't wait for the whole reply to finish synthesizing).
_SENT_END = re.compile(r"[。！？!?；;\n]")
PARTIAL_IDLE_COMMIT_SECONDS = 1.0
# Content-aware TTS: past this many spoken chars, or on code fences / markdown
# table separators, mute streaming playback and summarize at end of turn.
SPOKEN_BUDGET_CHARS = 400
SUMMARY_CUE = "回复内容较长，以下是关键信息摘要。"
SUMMARY_FALLBACK = "回复内容较长，详细内容请查看屏幕。"
_DATA_BLOCK = re.compile(r"```|\|\s*:?-{3,}:?\s*\|")
_SUMMARY_SYSTEM_PROMPT = (
    "你是语音播报摘要器。把给定的回复内容提炼成一段中文口语化摘要，"
    "不超过两百字，只保留关键信息与结论。直接输出纯文本，"
    "不要使用任何 markdown 格式、列表符号或网址。"
)
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_AUTOLINK = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MD_LINE_PREFIX = re.compile(r"(?m)^\s{0,3}(?:#{1,6}|>|[-+*]|\d+[.)])\s+")
_HTML_TAG = re.compile(r"<[^>]+>")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _split_sentence(buffer: str) -> tuple[str | None, str]:
    """Return (sentence, rest) at the first sentence-ending punctuation, else (None, buffer)."""
    m = _SENT_END.search(buffer)
    if m:
        return buffer[: m.end()], buffer[m.end() :]
    return None, buffer


def markdown_to_speech(text: str) -> str:
    """Turn streamed Markdown into natural text without changing the UI reply."""
    text = html.unescape(text)
    text = _MD_IMAGE.sub(r"\1", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_AUTOLINK.sub("", text)
    text = _HTML_TAG.sub("", text)
    text = _MD_LINE_PREFIX.sub("", text)
    # Formatting markers must not reach TTS (for example, ``**`` = "星星").
    text = re.sub(r"[*_~`#>|]+", "", text)
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


class VoiceSession:
    """One per WS connection. Held in memory; not persisted between reconnects."""

    def __init__(
        self,
        ws: WebSocket,
        user_id: str,
        *,
        cfg: VoiceRuntimeConfig,
        asr_factory: Callable[[], STTProvider],
        tts_factory: Callable[[], TTSProvider],
        principal: ApiKeyPrincipal | None = None,
    ) -> None:
        self.ws = ws
        self.user_id = user_id
        self.cfg = cfg
        # External embed channel identity (ticket-redeemed). None = internal JWT
        # user, for which none of the ext gates below apply.
        self._principal = principal
        self.agent_id: str | None = None
        self.session_id: str | None = None
        self.state: str = P.STATE_IDLE
        self._asr_factory = asr_factory
        self._tts_factory = tts_factory
        self._asr: STTProvider | None = None
        self._tts: TTSProvider | None = None
        self._vad = create_vad(cfg.vad_mode, cfg.vad_threshold, cfg.vad_silence_ms)
        self._pending_clarification: bool = False
        self._turn_lock = asyncio.Lock()
        self._active_turn: TurnContext | None = None
        self._active_llm_task: asyncio.Task | None = None
        self._audio_frames_received = 0
        self._audio_max_rms = 0.0
        self._last_asr_partial = ""
        self._vad_commit_task: asyncio.Task | None = None
        self._partial_idle_task: asyncio.Task | None = None
        self._ptt: bool = False  # push-to-talk: utterance bounded by release, not VAD

    # ── inbound dispatch ──────────────────────────────────────────────

    async def on_control(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        mtype = msg.get("type")
        if mtype == P.CLIENT_VOICE_START:
            self.agent_id = msg.get("agent_id")
            self.session_id = msg.get("session_id")
            self._ptt = msg.get("mode") == "ptt"
            if not await self._agent_allows_voice():
                return
            # PTT press while the agent is mid-turn = barge-in, then listen.
            if self._ptt and (
                self._active_turn is not None
                or self.state in (P.STATE_THINKING, P.STATE_SPEAKING)
            ):
                await self._handle_interrupt()
            await self._start_listening()
        elif mtype == P.CLIENT_VOICE_STOP:
            self._ptt = False
            await self._stop_listening()
        elif mtype == P.CLIENT_VOICE_RELEASE:
            await self._release()
        elif mtype == P.CLIENT_INTERRUPT:
            await self._handle_interrupt()
        elif mtype == P.CLIENT_AUDIO_INFO:
            logger.info(
                "voice_audio_info "
                f"context_rate={msg.get('context_sample_rate')} "
                f"track_rate={msg.get('track_sample_rate')} "
                f"channels={msg.get('channel_count')} "
                f"enabled={msg.get('track_enabled')} "
                f"muted={msg.get('track_muted')} "
                f"state={msg.get('track_state')} "
                f"backend={msg.get('capture_backend')}"
            )
        elif mtype == P.CLIENT_PONG:
            pass

    async def _agent_allows_voice(self) -> bool:
        """Enforce the Agent-level capability even for direct WS clients."""
        if not self.agent_id:
            await self._send({"type": P.SERVER_ERROR, "content": "未指定语音对话 Agent"})
            return False

        # External channel: same gate as ext invoke — scope + key bindings +
        # published status. Uniform message to avoid agent enumeration.
        if self._principal:
            from app.core.errors import ForbiddenError

            try:
                self._principal.require_scope("agents:invoke")
                self._principal.require_agent_access(self.agent_id)
            except ForbiddenError:
                await self._send(
                    {"type": P.SERVER_ERROR, "content": "Agent 不存在或无权访问"}
                )
                return False

        from app.services.agent_service import AgentService

        agent = await AgentService.get_agent(self.agent_id)
        if agent is None:
            await self._send({"type": P.SERVER_ERROR, "content": "语音对话 Agent 不存在"})
            return False
        if self._principal:
            from app.models.agent import AgentStatus

            if agent.get("status") != AgentStatus.PUBLISHED.value:
                await self._send(
                    {"type": P.SERVER_ERROR, "content": "Agent 不存在或无权访问"}
                )
                return False
        if not agent.get("voice_enabled", False):
            await self._send(
                {"type": P.SERVER_ERROR, "content": "当前 Agent 未开启语音对话能力"}
            )
            return False
        return True

    async def on_audio(self, pcm16: bytes) -> None:
        """Binary PCM16@16k frame → feed ASR + local VAD (fast barge-in)."""
        self._audio_frames_received += 1
        sample_count = len(pcm16) // 2
        if sample_count:
            samples = struct.unpack(f"<{sample_count}h", pcm16)
            rms = (
                math.sqrt(sum(sample * sample for sample in samples) / sample_count)
                / 32768
            )
            self._audio_max_rms = max(self._audio_max_rms, rms)
        else:
            rms = 0.0
        if self._audio_frames_received == 1:
            logger.info(f"voice_audio_first_frame bytes={len(pcm16)} rms={rms:.5f}")
        if self._audio_frames_received % 50 == 0:
            logger.info(
                "voice_audio_stats "
                f"frames={self._audio_frames_received} "
                f"current_rms={rms:.5f} max_rms={self._audio_max_rms:.5f}"
            )
        if self._asr is not None:
            await self._asr.feed(pcm16)
        # PTT bounds utterances by the release event; skip VAD auto-commit and
        # barge-in so a mid-press pause doesn't end the turn early.
        if self._vad is not None and not self._ptt:
            evt = self._vad.feed(pcm16)
            if evt is Speech.START:
                logger.info("voice_vad_speech_start", user_id=self.user_id)
            elif evt is Speech.END:
                logger.info(
                    "voice_vad_speech_end",
                    user_id=self.user_id,
                    has_partial=bool(self._last_asr_partial.strip()),
                )
            # Always clear client playback when the user starts speaking. The
            # backend may have finished sending TTS while the browser still has
            # several seconds queued locally.
            if evt is Speech.START and self.state != P.STATE_IDLE:
                await self._handle_interrupt()
            elif (
                evt is Speech.END
                and self.state == P.STATE_LISTENING
                and self._last_asr_partial.strip()
                and (self._vad_commit_task is None or self._vad_commit_task.done())
            ):
                # The async ASR endpoint may keep returning partial results on
                # a long-lived stream without marking an utterance definite.
                # Local VAD supplies the missing turn boundary.
                self._vad_commit_task = asyncio.create_task(
                    self._commit_vad_utterance()
                )

    # ── listening lifecycle ───────────────────────────────────────────

    async def _start_listening(self, *, update_state: bool = True) -> None:
        try:
            self._asr = self._asr_factory()
            self._asr.bind(
                on_partial=self._on_asr_partial,
                on_final=self._on_asr_final,
                on_utterance_end=None,
                on_error=self._on_asr_error,
            )
            await self._asr.open()
        except Exception as e:
            logger.warning("voice_asr_open_failed", error=str(e))
            await self._send(
                {"type": P.SERVER_ERROR, "content": f"语音识别连接失败：{e}"}
            )
            self._asr = None
            return
        if update_state:
            await self._set_state(P.STATE_LISTENING)
            logger.info("voice_start", user_id=self.user_id, agent_id=self.agent_id)
        else:
            logger.info("voice_asr_restarted", user_id=self.user_id)

    async def _stop_listening(self) -> None:
        await self._close_asr()
        await self._set_state(P.STATE_IDLE)

    async def _release(self) -> None:
        """PTT release-to-send: commit the latest partial and end the utterance.

        The utterance boundary is the release event (not VAD silence), so we
        commit whatever ASR has so far and do not reopen the ASR stream —
        ``_run_turn`` returns to idle once the turn finishes.
        """
        if not self._ptt:
            return
        await self._close_asr()
        text = self._last_asr_partial.strip()
        self._last_asr_partial = ""
        if not text:
            # Held the button without speaking — quietly return to idle.
            await self._set_state(P.STATE_IDLE)
            return
        logger.info("voice_ptt_release", user_id=self.user_id, text=text)
        await self._send({"type": P.SERVER_TRANSCRIPT_FINAL, "content": text})
        if self.agent_id:
            task = asyncio.create_task(self._run_turn(text))
            self._active_llm_task = task
            task.add_done_callback(lambda t: self._clear_active_task(t))

    # ── ASR callbacks ─────────────────────────────────────────────────

    async def _on_asr_partial(self, text: str) -> None:
        self._last_asr_partial = text
        logger.debug("voice_asr_partial", user_id=self.user_id, text=text)
        await self._send({"type": P.SERVER_TRANSCRIPT_DELTA, "content": text})
        if self._ptt:
            return  # utterance bounded by release; no idle auto-commit
        if self._partial_idle_task and not self._partial_idle_task.done():
            self._partial_idle_task.cancel()
        self._partial_idle_task = asyncio.create_task(
            self._commit_after_partial_idle(text)
        )

    async def _on_asr_final(self, text: str) -> None:
        current = asyncio.current_task()
        if (
            self._partial_idle_task
            and self._partial_idle_task is not current
            and not self._partial_idle_task.done()
        ):
            self._partial_idle_task.cancel()
        self._partial_idle_task = None
        self._last_asr_partial = ""
        logger.info("voice_asr_final", user_id=self.user_id, text=text)
        await self._send({"type": P.SERVER_TRANSCRIPT_FINAL, "content": text})
        if self.agent_id and text.strip():
            task = asyncio.create_task(self._run_turn(text))
            self._active_llm_task = task
            task.add_done_callback(lambda t: self._clear_active_task(t))

    async def _on_asr_error(self, error: str) -> None:
        logger.warning("voice_asr_stream_error", user_id=self.user_id, error=error)
        await self._send(
            {"type": P.SERVER_ERROR, "content": f"语音识别流异常：{error}"}
        )
        await self._set_state(P.STATE_ERROR)

    async def _commit_after_partial_idle(self, expected_text: str) -> None:
        """Bound endpoint latency when noisy audio prevents local VAD from ending."""
        try:
            await asyncio.sleep(PARTIAL_IDLE_COMMIT_SECONDS)
            if (
                self.state == P.STATE_LISTENING
                and self._last_asr_partial.strip() == expected_text.strip()
            ):
                logger.info("voice_partial_idle_commit", user_id=self.user_id)
                await self._commit_vad_utterance()
        except asyncio.CancelledError:
            return

    async def _commit_vad_utterance(self) -> None:
        """Commit the latest partial on local silence and start a fresh ASR stream."""
        text = self._last_asr_partial.strip()
        if not text or self.state != P.STATE_LISTENING:
            return
        self._last_asr_partial = ""
        logger.info("voice_vad_commit", user_id=self.user_id, text=text)

        # Publish immediately. Reconnecting ASR can take several seconds and
        # must never delay transcript display or Agent execution.
        await self._on_asr_final(text)
        await self._close_asr()

        # A fresh upstream stream prevents the next result from containing the
        # previous utterance as a cumulative prefix. Don't overwrite a
        # thinking/speaking state when the connection becomes ready.
        if self.state != P.STATE_IDLE:
            await self._start_listening(update_state=False)

    def _clear_active_task(self, task: asyncio.Task) -> None:
        if self._active_llm_task is task:
            self._active_llm_task = None

    # ── brain + TTS turn (stages 3 + 4) ───────────────────────────────

    async def _ext_turn_gate(self) -> bool:
        """Per-turn gate for the external (API Key) channel.

        1. Rate limit — one turn counts like one invoke against the Key's
           sliding window (handshake deliberately doesn't consume quota).
        2. Credential re-check — introspection (Redis-cached) so a revoked
           user token kills an otherwise long-lived connection.
        3. Fresh ExtCallContext — every turn gets its own object; no reliance
           on ContextVar inheritance across task boundaries.

        Returns False when the turn must be skipped (error already sent).
        """
        principal = self._principal
        assert principal is not None  # caller checks

        from app.core.rate_limiter import check_rate_limit

        allowed, _remaining, _reset = await check_rate_limit(
            api_key_id=principal.key_id, limit=principal.rate_limit
        )
        if not allowed:
            await self._send(
                {
                    "type": P.SERVER_ERROR,
                    "code": "RATE_LIMIT_EXCEEDED",
                    "content": "请求频率超限，请稍后重试",
                }
            )
            return False

        if principal.user_token and principal.introspect_url:
            from app.services.user_auth_service import UserAuthService

            try:
                result = await UserAuthService.introspect(
                    principal.introspect_url, principal.user_token
                )
            except Exception:
                result = None
            if result is None or not result.active:
                await self._send(
                    {
                        "type": P.SERVER_ERROR,
                        "code": "EXT_USER_TOKEN_INVALID",
                        "content": "登录凭证已失效，请重新授权后再试",
                    }
                )
                with contextlib.suppress(Exception):
                    await self.ws.close(code=4401, reason="Authentication failed")
                return False

        from app.services.ext_api_call_log_service import (
            ExtCallContext,
            set_ext_call_context,
        )

        set_ext_call_context(ExtCallContext(
            api_key_id=principal.key_id,
            owner_user_id=principal.owner_user_id,
            endpoint="voice:realtime",
            start_time_ms=_now_ms(),
        ))
        return True

    async def _ext_session_allowed(self, session_id: str | None) -> bool:
        """External channel: verify an existing session belongs to this user.

        MUST run before ``_resolve_session`` — its first step add_message()s
        the user utterance into the target session, so a late check would
        already have written into someone else's conversation. Exact-match
        on ``user_id``, same rule as the ext session detail endpoint.
        """
        if not self._principal or not session_id:
            return True

        from app.services.session_service import SessionService

        doc = await SessionService.get_session(session_id)
        if doc is None or doc.get("user_id") != self.user_id:
            await self._send(
                {"type": P.SERVER_ERROR, "content": "会话不存在或无权访问"}
            )
            return False
        return True

    async def _run_turn(self, transcript: str) -> None:
        # External channel: per-turn gate BEFORE taking the lock — one turn
        # ≈ one invoke (rate window), plus a credential re-check so a token
        # revoked mid-connection cannot keep this WS channel alive.
        if self._principal and not await self._ext_turn_gate():
            return
        async with self._turn_lock:
            turn = TurnContext(transcript=transcript)
            if self.cfg.tts_enabled:
                turn.tts_queue = asyncio.Queue()
            self._active_turn = turn
            await self._set_state(P.STATE_THINKING)
            if turn.tts_queue is not None:
                turn.tts_task = asyncio.create_task(self._tts_pump(turn))
            turn_status = 200
            try:
                if self._pending_clarification:
                    # Previous turn ended on ask_clarification → resume the
                    # suspended graph with this utterance as the answer.
                    self._pending_clarification = False
                    await self._exec_resume(turn)
                else:
                    await self._exec_brain(turn)
                # Brain done — flush any trailing buffered text, then close TTS.
                if not turn._cancelled and turn.tts_queue is not None:
                    if turn.summary_mode:
                        # Long/data-dense reply was muted mid-stream — speak an
                        # LLM summary instead (the UI already has the full text).
                        await self._speak_summary(turn)
                    elif turn.tts_buffer.strip():
                        await turn.tts_queue.put(turn.tts_buffer)
                        turn.tts_buffer = ""
                    await turn.tts_queue.put(None)  # sentinel
                    if turn.tts_task is not None:
                        await turn.tts_task
            except asyncio.CancelledError:
                turn._cancelled = True
                await self._abort_tts(turn)
                logger.info("voice_turn_cancelled", user_id=self.user_id)
            except Exception as e:
                logger.exception("voice_turn_error")
                await self._send({"type": P.SERVER_ERROR, "content": f"执行失败：{e}"})
                await self._abort_tts(turn)
                turn_status = 500
            finally:
                if self._principal:
                    # WS bypasses ExtApiStatsMiddleware (HTTP-only) — record
                    # the turn into the Key's usage stats manually.
                    from app.services.api_key_stats_service import record_request

                    with contextlib.suppress(Exception):
                        await record_request(
                            self._principal.key_id, "voice:realtime", turn_status
                        )
                self._active_turn = None
                if self._ptt:
                    await self._set_state(P.STATE_IDLE)
                elif self.state != P.STATE_IDLE:
                    await self._set_state(P.STATE_LISTENING)

    async def _exec_brain(self, turn: TurnContext) -> None:
        # Local imports mirror AgentExecutionService (defer heavy deps).
        from app.engine.harness_integration import stream as harness_stream
        from app.schemas.execution import ExecutionRequest
        from app.services.agent_execution_service import (
            _assemble_messages,
            _build_initial_state,
            _build_system_prompt_checked,
            _persist_agent_message,
            _record_execution_log,
            _resolve_session,
        )
        from app.services.agent_service import AgentService

        agent_id = self.agent_id
        if not agent_id:
            await self._send({"type": P.SERVER_ERROR, "content": "未选择智能体"})
            return
        exec_doc = await AgentService.get_agent(agent_id)
        if exec_doc is None:
            await self._send(
                {"type": P.SERVER_ERROR, "content": f"Agent {agent_id} 不存在"}
            )
            return
        turn.agent_doc = exec_doc

        body = ExecutionRequest(input=turn.transcript, session_id=self.session_id)
        if not await self._ext_session_allowed(body.session_id):
            return
        self.session_id = await _resolve_session(agent_id, body, self.user_id)

        system_text = await _build_system_prompt_checked(exec_doc)
        messages = _assemble_messages(system_text, turn.transcript)
        request_id = uuid.uuid4().hex
        state = _build_initial_state(
            agent_id,
            self.session_id,
            self.user_id,
            request_id,
            [agent_id],
            None,
            messages,
            execution_path="react",
            total_tokens=0,
        )
        await self._send(
            {
                "type": P.SERVER_TURN_STARTED,
                "request_id": request_id,
                "session_id": self.session_id,
            }
        )

        start_ms = _now_ms()
        run_error: BaseException | None = None
        try:
            result = await harness_stream(
                exec_doc,
                state,
                on_event=lambda e: self._on_brain_event(e, turn),
                enable_thinking=False,
                legacy_records=[],
                user_token=self._principal.user_token if self._principal else None,
            )
            turn.usage = result.get("usage", {})
        except Exception as exc:
            run_error = exc
            raise
        finally:
            if turn.timeline:
                try:
                    await _persist_agent_message(
                        self.session_id, turn.timeline, token_usage=turn.usage
                    )
                except Exception as exc:
                    logger.warning("voice_persist_error", error=str(exc))
            with contextlib.suppress(Exception):
                await _record_execution_log(
                    user_id=self.user_id,
                    agent_id=self.agent_id,
                    session_id=self.session_id,
                    request_id=request_id,
                    start_time_ms=start_ms,
                    token_usage=turn.usage,
                    error=run_error,
                )
            await self._send(
                {
                    "type": P.SERVER_TURN_END,
                    "request_id": request_id,
                    "session_id": self.session_id,
                }
            )

    async def _on_brain_event(self, evt: dict, turn: TurnContext) -> None:
        """harness AppEvent (dict) → forward text to client + pump TTS."""
        if turn._cancelled:
            return
        turn.timeline.append(evt)
        t = evt.get("type")
        if t == "text_delta":
            content = evt.get("content", "")
            turn.reply_text += content
            await self._send({"type": P.SERVER_AGENT_TEXT_DELTA, "content": content})
            await self._feed_tts(content, turn)
        elif t == "error":
            await self._send(
                {
                    "type": P.SERVER_ERROR,
                    "content": evt.get("content", "出错了"),
                    "source": evt.get("source"),
                }
            )
        elif t == "interrupt":
            # Voice clarification: speak the question, mark pending so the next
            # utterance resumes the suspended graph instead of starting fresh.
            question = self._extract_interrupt_question(evt)
            self._pending_clarification = True
            await self._send({"type": P.SERVER_INTERRUPT_REQUEST, "question": question})
            await self._feed_tts(question, turn)
        # tool_call / tool_result are surfaced as agent text only for now.

    async def _exec_resume(self, turn: TurnContext) -> None:
        """Resume a graph suspended by ask_clarification, with this utterance as the answer.

        Mirrors ``_exec_brain`` but calls harness ``resume()`` (Command(resume=answer))
        so the suspended REACT context is reused — same persistence + logging path.
        """
        from app.engine.harness_integration import resume as harness_resume
        from app.services.agent_execution_service import (
            _build_initial_state,
            _persist_agent_message,
            _record_execution_log,
        )
        from app.services.agent_service import AgentService

        agent_id = self.agent_id
        if not agent_id:
            await self._send({"type": P.SERVER_ERROR, "content": "未选择智能体"})
            return
        exec_doc = await AgentService.get_agent(agent_id)
        if exec_doc is None:
            await self._send(
                {"type": P.SERVER_ERROR, "content": f"Agent {agent_id} 不存在"}
            )
            return
        turn.agent_doc = exec_doc

        if not await self._ext_session_allowed(self.session_id):
            return

        request_id = uuid.uuid4().hex
        state = _build_initial_state(
            agent_id,
            self.session_id,
            self.user_id,
            request_id,
            [agent_id],
            None,
            [],
            execution_path="react",
            total_tokens=0,
        )
        await self._send(
            {
                "type": P.SERVER_TURN_STARTED,
                "request_id": request_id,
                "session_id": self.session_id,
            }
        )
        start_ms = _now_ms()
        run_error: BaseException | None = None
        try:
            result = await harness_resume(
                exec_doc,
                state,
                on_event=lambda e: self._on_brain_event(e, turn),
                answer=turn.transcript,
                enable_thinking=False,
                user_token=self._principal.user_token if self._principal else None,
            )
            turn.usage = result.get("usage", {})
        except Exception as exc:
            run_error = exc
            raise
        finally:
            if turn.timeline:
                try:
                    await _persist_agent_message(
                        self.session_id, turn.timeline, token_usage=turn.usage
                    )
                except Exception as exc:
                    logger.warning("voice_persist_error", error=str(exc))
            with contextlib.suppress(Exception):
                await _record_execution_log(
                    user_id=self.user_id,
                    agent_id=self.agent_id,
                    session_id=self.session_id,
                    request_id=request_id,
                    start_time_ms=start_ms,
                    token_usage=turn.usage,
                    error=run_error,
                )
            await self._send(
                {
                    "type": P.SERVER_TURN_END,
                    "request_id": request_id,
                    "session_id": self.session_id,
                }
            )

    @staticmethod
    def _extract_interrupt_question(evt: dict) -> str:
        for key in ("question", "clarification", "workflow_confirmation"):
            v = evt.get(key)
            if isinstance(v, dict):
                q = v.get("question") or v.get("message")
                if q:
                    return str(q)
            elif isinstance(v, str) and v:
                return v
        return "请补充更多信息"

    # ── TTS pump (stage 4) ─────────────────────────────────────────────

    async def _feed_tts(self, delta: str, turn: TurnContext) -> None:
        """Sentence-buffer the text_delta; enqueue whole sentences for synthesis.

        Content-aware switch: once the spoken budget is exhausted or a data
        block (code fence / table separator) appears, mute TTS via summary
        mode — the rest of the reply is only summarized at end of turn. The
        client UI is fed separately (SERVER_AGENT_TEXT_DELTA) and unaffected.
        """
        if turn.tts_queue is None or turn.summary_mode:
            return
        turn.tts_buffer += delta
        if _DATA_BLOCK.search(turn.tts_buffer):
            await self._enter_summary_mode(turn)
            return
        while True:
            sentence, turn.tts_buffer = _split_sentence(turn.tts_buffer)
            if sentence is None:
                break
            turn.spoken_chars += len(sentence)
            await turn.tts_queue.put(sentence)
        if turn.spoken_chars > SPOKEN_BUDGET_CHARS:
            await self._enter_summary_mode(turn)

    async def _enter_summary_mode(self, turn: TurnContext) -> None:
        """Mute streaming TTS; announce the switch, summarize at end of turn."""
        turn.summary_mode = True
        turn.tts_buffer = ""
        logger.info(
            "voice_tts_summary_mode",
            user_id=self.user_id,
            spoken_chars=turn.spoken_chars,
        )
        if turn.tts_queue is not None:
            await turn.tts_queue.put(SUMMARY_CUE)

    async def _speak_summary(self, turn: TurnContext) -> None:
        """Speak a short LLM summary of a muted long reply, sentence by sentence.

        Uses the Agent's own model (``turn.agent_doc``); any failure degrades
        to the fallback cue so the turn still ends with spoken feedback.
        """
        assert turn.tts_queue is not None
        text = ""
        if turn.reply_text.strip():
            try:
                from langchain_core.messages import HumanMessage, SystemMessage

                from app.engine.llm_factory import get_llm_client

                client = await get_llm_client(turn.agent_doc)
                reply = await client.ainvoke(
                    [
                        SystemMessage(content=_SUMMARY_SYSTEM_PROMPT),
                        HumanMessage(content=turn.reply_text),
                    ]
                )
                content = reply.content if hasattr(reply, "content") else reply
                raw = content if isinstance(content, str) else str(content)
                text = markdown_to_speech(raw)
            except Exception as e:
                logger.warning("voice_tts_summary_failed", error=str(e))
        if not text:
            text = SUMMARY_FALLBACK
        buffer = text
        while buffer:
            sentence, buffer = _split_sentence(buffer)
            if sentence is None:
                if buffer.strip():
                    await turn.tts_queue.put(buffer)
                break
            await turn.tts_queue.put(sentence)

    async def _tts_pump(self, turn: TurnContext) -> None:
        """Consume the per-turn TTS queue: synth each sentence and stream PCM down.

        Runs as its own task so harness streaming (in _exec_brain) keeps flowing
        text_delta into the queue while earlier sentences are already being
        spoken — that is what keeps first-packet latency low.
        """
        if self._tts is None:
            self._tts = self._tts_factory()
        tts = self._tts
        assert turn.tts_queue is not None
        try:
            while True:
                sentence = await turn.tts_queue.get()
                if sentence is None or turn._cancelled:
                    break
                if not sentence.strip():
                    continue
                sentence = markdown_to_speech(sentence)
                if not sentence:
                    continue
                if self.state != P.STATE_SPEAKING:
                    await self._set_state(P.STATE_SPEAKING)
                try:
                    async for pcm in tts.synth_stream(sentence):
                        if turn._cancelled:
                            break
                        await self.ws.send_bytes(pcm)
                except Exception as e:
                    logger.warning(f"voice_tts_synth_error error={e}")
                    await self._send(
                        {
                            "type": P.SERVER_ERROR,
                            "content": f"语音合成失败：{e}",
                        }
                    )
                    break
        except asyncio.CancelledError:
            raise

    # ── barge-in (stage 4 impl; stage 5 wires VAD) ────────────────────

    async def _handle_interrupt(self) -> None:
        """Double-layer barge-in: tell client to stop playback + abort the turn."""
        # Clear first, even if the server-side turn is already complete: the
        # browser may still have scheduled AudioBufferSourceNodes playing.
        await self._send({"type": P.SERVER_PLAYBACK_CLEAR})
        turn = self._active_turn
        if turn is None:
            logger.info("voice_playback_cleared", user_id=self.user_id)
            return
        turn._cancelled = True
        if self._active_llm_task and not self._active_llm_task.done():
            self._active_llm_task.cancel()  # stop generation
        await self._abort_tts(turn)  # stop synthesis
        await self._set_state(P.STATE_LISTENING)
        logger.info("voice_interrupt", user_id=self.user_id)

    async def _abort_tts(self, turn: TurnContext) -> None:
        if turn.tts_task and not turn.tts_task.done():
            turn.tts_task.cancel()
        if self._tts is not None:
            with contextlib.suppress(Exception):
                await self._tts.stop()

    # ── helpers ────────────────────────────────────────────────────────

    async def _set_state(self, state: str) -> None:
        self.state = state
        await self._send({"type": P.SERVER_VOICE_STATE, "state": state})

    async def _send(self, payload: dict[str, Any]) -> None:
        await self.ws.send_text(json.dumps(payload, ensure_ascii=False))

    async def _close_asr(self) -> None:
        if self._asr is not None:
            with contextlib.suppress(Exception):
                await self._asr.close()
            self._asr = None

    async def close(self) -> None:
        """Cancel any in-flight turn, then release ASR + TTS."""
        if self._vad_commit_task and not self._vad_commit_task.done():
            self._vad_commit_task.cancel()
        if self._partial_idle_task and not self._partial_idle_task.done():
            self._partial_idle_task.cancel()
        if self._active_llm_task and not self._active_llm_task.done():
            self._active_llm_task.cancel()
        if (
            self._active_turn
            and self._active_turn.tts_task
            and not self._active_turn.tts_task.done()
        ):
            self._active_turn.tts_task.cancel()
        if self._vad is not None:
            with contextlib.suppress(Exception):
                self._vad.close()
        await self._close_asr()
        if self._tts is not None:
            with contextlib.suppress(Exception):
                await self._tts.close()
            self._tts = None
