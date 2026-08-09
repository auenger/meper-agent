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
import json
import re
import time
import uuid
from typing import Any, Callable

from fastapi import WebSocket
from loguru import logger

from app.voice import protocol as P
from app.voice.config import VoiceRuntimeConfig
from app.voice.providers.base import STTProvider, TTSProvider
from app.voice.turn import TurnContext
from app.voice.vad import Speech, create_vad

# Sentence-ending punctuation — flush a TTS chunk when one is seen, to cut
# first-packet latency (don't wait for the whole reply to finish synthesizing).
_SENT_END = re.compile(r"[。！？!?；;\n]")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _split_sentence(buffer: str) -> tuple[str | None, str]:
    """Return (sentence, rest) at the first sentence-ending punctuation, else (None, buffer)."""
    m = _SENT_END.search(buffer)
    if m:
        return buffer[: m.end()], buffer[m.end() :]
    return None, buffer


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
    ) -> None:
        self.ws = ws
        self.user_id = user_id
        self.cfg = cfg
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
            await self._start_listening()
        elif mtype == P.CLIENT_VOICE_STOP:
            await self._stop_listening()
        elif mtype == P.CLIENT_INTERRUPT:
            await self._handle_interrupt()
        elif mtype == P.CLIENT_PONG:
            pass

    async def on_audio(self, pcm16: bytes) -> None:
        """Binary PCM16@16k frame → feed ASR + local VAD (fast barge-in)."""
        if self._asr is not None:
            await self._asr.feed(pcm16)
        if self._vad is not None:
            evt = self._vad.feed(pcm16)
            # Only speech-start during speaking triggers barge-in; the ASR
            # provider handles the utterance-end (turn boundary).
            if evt is Speech.START and self.state == P.STATE_SPEAKING:
                await self._handle_interrupt()

    # ── listening lifecycle ───────────────────────────────────────────

    async def _start_listening(self) -> None:
        try:
            self._asr = self._asr_factory()
            self._asr.bind(
                on_partial=self._on_asr_partial,
                on_final=self._on_asr_final,
                on_utterance_end=None,
            )
            await self._asr.open()
        except Exception as e:
            logger.warning("voice_asr_open_failed", error=str(e))
            await self._send({"type": P.SERVER_ERROR, "content": f"语音识别连接失败：{e}"})
            self._asr = None
            return
        await self._set_state(P.STATE_LISTENING)
        logger.info("voice_start", user_id=self.user_id, agent_id=self.agent_id)

    async def _stop_listening(self) -> None:
        await self._close_asr()
        await self._set_state(P.STATE_IDLE)

    # ── ASR callbacks ─────────────────────────────────────────────────

    async def _on_asr_partial(self, text: str) -> None:
        await self._send({"type": P.SERVER_TRANSCRIPT_DELTA, "content": text})

    async def _on_asr_final(self, text: str) -> None:
        await self._send({"type": P.SERVER_TRANSCRIPT_FINAL, "content": text})
        if self.agent_id and text.strip():
            task = asyncio.create_task(self._run_turn(text))
            self._active_llm_task = task
            task.add_done_callback(lambda t: self._clear_active_task(t))

    def _clear_active_task(self, task: asyncio.Task) -> None:
        if self._active_llm_task is task:
            self._active_llm_task = None

    # ── brain + TTS turn (stages 3 + 4) ───────────────────────────────

    async def _run_turn(self, transcript: str) -> None:
        async with self._turn_lock:
            turn = TurnContext(transcript=transcript)
            turn.tts_queue = asyncio.Queue()
            self._active_turn = turn
            await self._set_state(P.STATE_THINKING)
            turn.tts_task = asyncio.create_task(self._tts_pump(turn))
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
                    if turn.tts_buffer.strip():
                        await turn.tts_queue.put(turn.tts_buffer)
                        turn.tts_buffer = ""
                    await turn.tts_queue.put(None)  # sentinel
                    await turn.tts_task
            except asyncio.CancelledError:
                turn._cancelled = True
                await self._abort_tts(turn)
                logger.info("voice_turn_cancelled", user_id=self.user_id)
            except Exception as e:
                logger.exception("voice_turn_error")
                await self._send({"type": P.SERVER_ERROR, "content": f"执行失败：{e}"})
                await self._abort_tts(turn)
            finally:
                self._active_turn = None
                if self.state != P.STATE_IDLE:
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

        exec_doc = await AgentService.get_agent(self.agent_id)
        if exec_doc is None:
            await self._send({"type": P.SERVER_ERROR, "content": f"Agent {self.agent_id} 不存在"})
            return

        body = ExecutionRequest(input=turn.transcript, session_id=self.session_id)
        self.session_id = await _resolve_session(self.agent_id, body, self.user_id)

        system_text = await _build_system_prompt_checked(exec_doc)
        messages = _assemble_messages(system_text, turn.transcript)
        request_id = uuid.uuid4().hex
        state = _build_initial_state(
            self.agent_id, self.session_id, self.user_id, request_id,
            [self.agent_id], None, messages, execution_path="react", total_tokens=0,
        )
        await self._send({
            "type": P.SERVER_TURN_STARTED, "request_id": request_id, "session_id": self.session_id,
        })

        start_ms = _now_ms()
        run_error: BaseException | None = None
        try:
            result = await harness_stream(
                exec_doc, state,
                on_event=lambda e: self._on_brain_event(e, turn),
                enable_thinking=False, legacy_records=[], user_token=None,
            )
            turn.usage = result.get("usage", {})
        except Exception as exc:
            run_error = exc
            raise
        finally:
            if turn.timeline:
                try:
                    await _persist_agent_message(self.session_id, turn.timeline, token_usage=turn.usage)
                except Exception as exc:
                    logger.warning("voice_persist_error", error=str(exc))
            try:
                await _record_execution_log(
                    user_id=self.user_id, agent_id=self.agent_id, session_id=self.session_id,
                    request_id=request_id, start_time_ms=start_ms,
                    token_usage=turn.usage, error=run_error,
                )
            except Exception:
                pass
            await self._send({
                "type": P.SERVER_TURN_END, "request_id": request_id, "session_id": self.session_id,
            })

    async def _on_brain_event(self, evt: dict, turn: TurnContext) -> None:
        """harness AppEvent (dict) → forward text to client + pump TTS."""
        if turn._cancelled:
            return
        turn.timeline.append(evt)
        t = evt.get("type")
        if t == "text_delta":
            content = evt.get("content", "")
            await self._send({"type": P.SERVER_AGENT_TEXT_DELTA, "content": content})
            await self._feed_tts(content, turn)
        elif t == "error":
            await self._send({
                "type": P.SERVER_ERROR, "content": evt.get("content", "出错了"),
                "source": evt.get("source"),
            })
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

        exec_doc = await AgentService.get_agent(self.agent_id)
        if exec_doc is None:
            await self._send({"type": P.SERVER_ERROR, "content": f"Agent {self.agent_id} 不存在"})
            return

        request_id = uuid.uuid4().hex
        state = _build_initial_state(
            self.agent_id, self.session_id, self.user_id, request_id,
            [self.agent_id], None, [], execution_path="react", total_tokens=0,
        )
        await self._send({
            "type": P.SERVER_TURN_STARTED, "request_id": request_id, "session_id": self.session_id,
        })
        start_ms = _now_ms()
        run_error: BaseException | None = None
        try:
            result = await harness_resume(
                exec_doc, state,
                on_event=lambda e: self._on_brain_event(e, turn),
                answer=turn.transcript, enable_thinking=False, user_token=None,
            )
            turn.usage = result.get("usage", {})
        except Exception as exc:
            run_error = exc
            raise
        finally:
            if turn.timeline:
                try:
                    await _persist_agent_message(self.session_id, turn.timeline, token_usage=turn.usage)
                except Exception as exc:
                    logger.warning("voice_persist_error", error=str(exc))
            try:
                await _record_execution_log(
                    user_id=self.user_id, agent_id=self.agent_id, session_id=self.session_id,
                    request_id=request_id, start_time_ms=start_ms,
                    token_usage=turn.usage, error=run_error,
                )
            except Exception:
                pass
            await self._send({
                "type": P.SERVER_TURN_END, "request_id": request_id, "session_id": self.session_id,
            })

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
        """Sentence-buffer the text_delta; enqueue whole sentences for synthesis."""
        if turn.tts_queue is None:
            return
        turn.tts_buffer += delta
        while True:
            sentence, turn.tts_buffer = _split_sentence(turn.tts_buffer)
            if sentence is None:
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
                if self.state != P.STATE_SPEAKING:
                    await self._set_state(P.STATE_SPEAKING)
                try:
                    async for pcm in tts.synth_stream(sentence):
                        if turn._cancelled:
                            break
                        await self.ws.send_bytes(pcm)
                except Exception as e:
                    logger.warning("voice_tts_synth_error", error=str(e))
                    break
        except asyncio.CancelledError:
            raise

    # ── barge-in (stage 4 impl; stage 5 wires VAD) ────────────────────

    async def _handle_interrupt(self) -> None:
        """Double-layer barge-in: tell client to stop playback + abort the turn."""
        turn = self._active_turn
        if turn is None:
            return
        turn._cancelled = True
        await self._send({"type": P.SERVER_PLAYBACK_CLEAR})  # client stops audio
        if self._active_llm_task and not self._active_llm_task.done():
            self._active_llm_task.cancel()  # stop generation
        await self._abort_tts(turn)  # stop synthesis
        await self._set_state(P.STATE_LISTENING)
        logger.info("voice_interrupt", user_id=self.user_id)

    async def _abort_tts(self, turn: TurnContext) -> None:
        if turn.tts_task and not turn.tts_task.done():
            turn.tts_task.cancel()
        if self._tts is not None:
            try:
                await self._tts.stop()
            except Exception:
                pass

    # ── helpers ────────────────────────────────────────────────────────

    async def _set_state(self, state: str) -> None:
        self.state = state
        await self._send({"type": P.SERVER_VOICE_STATE, "state": state})

    async def _send(self, payload: dict[str, Any]) -> None:
        await self.ws.send_text(json.dumps(payload, ensure_ascii=False))

    async def _close_asr(self) -> None:
        if self._asr is not None:
            try:
                await self._asr.close()
            except Exception:
                pass
            self._asr = None

    async def close(self) -> None:
        """Cancel any in-flight turn, then release ASR + TTS."""
        if self._active_llm_task and not self._active_llm_task.done():
            self._active_llm_task.cancel()
        if self._active_turn and self._active_turn.tts_task and not self._active_turn.tts_task.done():
            self._active_turn.tts_task.cancel()
        if self._vad is not None:
            try:
                self._vad.close()
            except Exception:
                pass
        await self._close_asr()
        if self._tts is not None:
            try:
                await self._tts.close()
            except Exception:
                pass
            self._tts = None
