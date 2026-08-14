"""Voice realtime WS protocol — message type constants.

Wire format on the single WS ``/api/v1/voice/realtime``:
- Text frames: JSON control messages (one ``type`` from the constants below).
- Binary frames: raw PCM16 little-endian audio (upstream 16kHz, downstream 24kHz).

Keeping this as plain constants (not Pydantic models) matches the looseness of
the existing notification WS protocol and avoids over-modelling a wire format
that is dispatch-by-type at both ends.
"""
from __future__ import annotations

# ── Client → Server (text frames) ─────────────────────────────────────
CLIENT_VOICE_START = "voice.start"     # {agent_id, session_id?, mode?} mode="ptt" = push-to-talk
CLIENT_VOICE_STOP = "voice.stop"       # close mic, leave voice mode
CLIENT_VOICE_RELEASE = "voice.release"  # PTT: release-to-send — commit partial, end utterance
CLIENT_INTERRUPT = "interrupt"       # manual barge-in button
CLIENT_PONG = "pong"                 # heartbeat reply
CLIENT_AUDIO_INFO = "audio.info"     # browser capture sample-rate diagnostics

# ── Server → Client (text frames) ─────────────────────────────────────
SERVER_VOICE_STATE = "voice.state"           # {state}
SERVER_TRANSCRIPT_DELTA = "transcript.delta"  # {content} ASR partial
SERVER_TRANSCRIPT_FINAL = "transcript.final"  # {content} ASR sentence final
SERVER_AGENT_TEXT_DELTA = "agent_text_delta"  # {content} LLM text increment
SERVER_TURN_STARTED = "turn.started"          # {request_id, session_id}
SERVER_TURN_END = "turn.end"                  # {request_id, session_id}
SERVER_PLAYBACK_CLEAR = "playback.clear"      # barge-in: stop local playback
SERVER_INTERRUPT_REQUEST = "interrupt.request"  # {question} voice clarification
SERVER_PING = "ping"                          # heartbeat
SERVER_ERROR = "error"                        # {content, source?}

# ── Voice session states ──────────────────────────────────────────────
STATE_IDLE = "idle"
STATE_LISTENING = "listening"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"
STATE_ERROR = "error"

# ── Audio format (PCM16 mono) ─────────────────────────────────────────
INPUT_SAMPLE_RATE = 16000   # upstream (mic → ASR)
OUTPUT_SAMPLE_RATE = 24000  # downstream (TTS → speaker)
