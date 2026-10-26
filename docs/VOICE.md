# Voice input

## Where voice is written out (owner, 2026-10-25)

Hardware-heavy work does not run on the Pi. The Pi CPU tier is removed: the Pi never transcribes.
- **Atlas on the PC:** Quick note 🎙 sends the recording to the PC's own Atlas service, which runs Whisper
  (faster-whisper) on the PC: on an NVIDIA GPU when one is usable (large-v3-turbo), otherwise on the CPU (small).
  Nothing leaves the PC and recordings are not kept (`services/atlas/localvoice.py`). Set up under
  **Systems › Voice › Whisper on this PC**: Install (pip --user, with optional NVIDIA libraries), model, language,
  words to spell right. Refused on a Pi and with `ATLAS_LOCAL_VOICE=off`.
- **The phone and Telegram** use the Engine's tiers: a Whisper server (`HQ_WHISPER_URL`, e.g. on the PC) or a
  hosted API; otherwise the browser's own dictation.
- Atlas decodes the recording itself (PyAV → 16 kHz mono), because faster-whisper's own decoder passes an option
  PyAV 14 removed ("open() got an unexpected keyword argument 'metadata_errors'").
- NVIDIA support (about 1 GB) is pre-selected only when the hardware scan finds an NVIDIA GPU; installed libraries
  without such a GPU can be removed from the card. The install shows its progress and survives a restart.
- **Browser dictation** stays the no-setup option (Chrome and Edge send audio to their own speech service).
- The Claude app's dictation is part of the Claude apps and cannot be called by other apps.
- Systems › Voice › "How Quick note hears you" picks the order per device; the small PC / W / B button next to 🎙 switches.

## Why the Engine transcribed worse than the Claude app

| | Claude app | Engine before (task #63) |
|---|---|---|
| Capture | Records the whole utterance, then uploads it | Dashboard: the browser's Web Speech API streams short fragments |
| Model | Large server-side speech model | Whatever the phone's browser vendor provides; Telegram: Whisper `small` |
| Context | Transcribes the full recording at once, so later words disambiguate earlier ones | Each fragment recognised separately, no punctuation across fragments |
| Language | Detected from the audio | Taken from the phone's UI language (`navigator.language`): Dutch speech on an English phone was recognised as English |
| Pauses | Recording continues until you stop | Chrome on Android ends `continuous` sessions after a short silence and repeats or drops words |
| Vocabulary | General | No hint, so names like HQ, Djeddi, Tailscale were misspelt |

## What the Engine does now

1. **Talk records, Whisper writes it out (Claude-app style).** When a speech-to-text tier is configured, the
   Talk button records with `MediaRecorder` (echo cancellation, noise suppression, auto gain), uploads the
   whole clip to `POST /api/voice`, and `models.transcribe` runs Whisper on it in one batch. The recording is
   kept in `memory/voice/`; if transcription fails it is noted in memory, as for Telegram voice notes.
2. **Language hint.** A small NL / EN / Auto selector next to Talk (remembered on the phone). Default comes
   from `stt_language` in the constitution; Auto lets Whisper detect it.
3. **Better model and vocabulary.** `whisper_model = "large-v3-turbo"` (was `small`) and `stt_prompt` with
   names Whisper should spell right. Telegram voice notes use the same path.
4. **Fallback: browser speech, fixed.** Without a server tier the browser recogniser is used, but with the
   chosen language, live interim text, and an automatic restart after each pause instead of `continuous` mode.

## One voice path in Atlas (task #143, with #12 one-tap voice and #14 Whisper on the GPU PC)

Quick note 🎙 in Atlas works like dictation in the Claude app: tap, talk, watch the transcript appear live
under the note, tap again; the text lands in the note to edit, and nothing is sent until you press Send.
- **Hearing.** The browser's speech recognition (instant, interim words) or Whisper through the Engine
  (`engine.brain.voice_input` on the hub → `engine/talk.py` `transcribe` → the tiers below). With Whisper the
  recording so far is sent every 3 s as a preview (`partial`: never kept, never logged; previews stop after two
  minutes), and the whole recording at Stop (kept in `memory/voice/`; if no tier answers it goes to the backlog
  below and the note says "Recording saved"). The small B / W button next to 🎙 switches, remembered per device;
  it only shows when both are available. The browser keeps listening through pauses until you tap Stop.
- **Send as.** With text in the note: Auto (a question is answered, anything else is filed), Ask (Brain chat),
  File (brain dump, with Undo). `engine.brain.talk` takes `mode` `auto|ask|file`.
- The old dashboard's `/api/voice` and Telegram voice notes use the same `talk.transcribe` / `keep_untranscribed`.

## Tiers (`[models.routing] transcribe`)

- **local** – Whisper on the GPU PC: set `HQ_WHISPER_URL` in `.env` to an OpenAI-compatible server, e.g.
  `faster-whisper-server` / `speaches` on port 8000. Free and private; needs the PC awake.
- **cloud** – any OpenAI-compatible speech-to-text service: set `HQ_STT_CLOUD_URL` (base URL, without
  `/v1/...`) and `HQ_STT_CLOUD_KEY` in `.env`, and `cloud_stt_model` in the constitution. Off until both are
  set; it costs money and sends audio to that provider, so it is Djeddi's choice. Respects the kill switch.

## Backlog: recordings made while no tier was reachable (`engine/voiceback.py`)

When every tier fails, the recording stays in `memory/voice/` and a memory row tagged `voice untranscribed` is
written (Talk and Telegram alike). `hq cycle` (hourly, cron) retries up to `stt_backlog_per_run` of them, oldest
first, once a tier answers, and puts each transcript into the brain dump (`source` "voice"). `hq voice backlog`
runs the same retry now.

- An unreachable tier stops the run; nothing is counted against the recording.
- A tier that answers but rejects the file, or returns no text, counts a strike; after three the recording is
  tagged `voice failed` and left on disk to listen to.
- A recording deleted from disk is tagged `voice missing`.
- Re-transcription uses the same routing as live recordings, so a configured hosted tier is used for the backlog
  too (it costs money and sends audio to that provider, as it does for live recordings).

## Setup from the app (System → Voice · Whisper)

Pick a tier, enter its address (and key for the hosted API), press **Save**, then **Test**. The same from SSH:
`./atlas.sh voice status`, `./atlas.sh voice set gpu http://100.x.y.z:8000`, `./atlas.sh voice set cloud https://api.openai.com`
(asks for the key without echo), `./atlas.sh voice test gpu|pi|cloud`, `./atlas.sh voice remove gpu|cloud`.

- Addresses and keys are written to `.env` only; keys are never shown, logged or stored in the database.
- The dashboard uses a new setting at once; the Telegram bot after its next restart (`bash deploy/restart.sh`).
- **GPU PC:** run an OpenAI-compatible Whisper server (speaches / faster-whisper-server) on port 8000. Ollama does
  not serve Whisper. The test transcribes your latest recording from `memory/voice/` (it stays on the tailnet).
- **Pi CPU:** removed on 2026-10-25 (too slow); `pi_whisper_model` in the constitution is no longer read.
- **Hosted API:** the test sends a one-second silent clip, never a recording, and respects the kill switch.

Check on the Pi: `./atlas.sh serve`, open the https address, and look at the hint under the text box: it says
either "Whisper (local / cloud) writes it out" or "Speech by your browser".
