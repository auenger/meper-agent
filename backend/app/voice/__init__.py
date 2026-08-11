"""Voice realtime conversation module — independent, zero-impact on existing chat.

Design borrows the qwen-audio-agent three-layer realtime architecture
(voice frontend <-> state machine: VAD/endpoint/barge-in/turns <-> STT+LLM+TTS)
but is fully self-contained inside meper-agent (no external voice runtime).

Build-up stages:
  1. WS skeleton + audio echo (current)
  2. Volcano streaming ASR (live transcription)
  3. Harness brain (LLM text streaming, reusing Session/Message)
  4. Volcano streaming TTS (speech playback)
  5. Barge-in interruption (silero VAD)
  6. Frontend voice UI
  7. Audio persistence + voice clarification loop
"""
