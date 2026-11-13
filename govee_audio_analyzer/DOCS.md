# Govee Sendspin Audio Analyzer v0.4.0

## Input and processing

Receives **22,050 Hz, 16-bit, mono PCM** from the Music Assistant Sendspin output player, processes 2,205 samples (100 ms) at a time, and publishes one non-retained MQTT JSON message per hop to `govee_audio/state`. The long FFT is 8,192 samples for accurate low-frequency resolution; a shorter overlapping FFT is used for transient candidates. Analysis timestamps are based on input sample count rather than wall-clock time. Direct audio is not stored on disk.

**This is a receiver, not a decoder:** Music Assistant must send PCM in the declared format. The add-on's entry point is `run.py`, which runs `sendspin_receiver.py`. The standalone analyzer's `AUDIO_INPUT` FFmpeg mode is available for command-line testing, not the add-on's normal playback path.

## MQTT keys / Home Assistant entities

All numeric sensor keys use `homeassistant/sensor/govee_audio_<key>/config`, numeric fields live in the JSON on `govee_audio/state`; binary keys use `homeassistant/binary_sensor/govee_audio_<key>/config` and evaluate ON/OFF. The existing MQTT unique IDs are unchanged.

Original fields: `energy`, `rms`, `bass`, `mid`, `treble`, `trend`, `onset`, `buildup`, `drop`.

Added numeric fields:

- `instant_rms`, `peak_amplitude`: per-100-ms amplitude measurements.
- `sub_bass_energy`, `bass_energy`, `mid_energy`, `treble_energy`: independent 0..1 levels using adaptive peaks; **not calibrated physical or absolute loudness**.
- `spectral_flux`, `onset_strength`, `onset_density`: transient and attack indicators.
- `spectral_centroid_hz`, `spectral_rolloff_hz`, `spectral_flatness`: spectral shape descriptors.
- `bpm`, `beat_confidence`, `beat_phase`, `tempo_stability`: estimated pulse timing/tempo. Tempo can confuse subdivisions, swing, or complex rhythmic patterns.
- `kick_strength`, `snare_strength`, `hihat_strength`: relative spectral attack scores at each detected onset.
- `dynamic_range_db`: short-term energy percentile ratio in decibels, **not** studio loudness range / calibrated dB SPL.
- `buildup_score`, `drop_score`: heuristic scores, not definitive musical structure recognition.

Added binary keys: `beat`, `kick_candidate`, `snare_candidate`, `hihat_candidate`. Candidate flags are not definitive instrument identification. Short events may last only one 100-ms MQTT publication and be missed by slower Home Assistant automations.

The existing `bass`, `mid`, `treble` remain ratios summing to approximately 1, while `bass_energy` and similar values indicate independent adaptive band activity. The older `rms` remains a longer-window raw RMS, whereas `instant_rms` responds to each new 100-ms block. Both raw RMS fields are uncalibrated.

## Home Assistant setup

1. Install Music Assistant and an MQTT broker (such as Mosquitto). Set a dedicated MQTT account, if required.
2. Add this GitHub add-on repository under **Settings → Apps → Add-on store/Install app → ⋮ → Repositories**, depending on HA version; add the URL.
3. Install **Govee Sendspin Audio Analyzer** and configure `sendspin_server` (for example `ws://YOUR_MUSIC_ASSISTANT_HOST:8927/sendspin`) and MQTT credentials.
4. Start the app and check the private logs for Sendspin pairing information and `Sendspin connected`.
5. In Music Assistant, enable and direct playback to the **Govee Audio Analyzer** player (or group it with your speaker if supported).
6. Look up actual entity IDs in Home Assistant: the generated entity ID can vary when an entity with a matching name already exists. The v3 automation under `examples/` assumes names beginning `sensor.govee_audio_analyzer_audio_`.

The addon retains a persistent `identity.key` and `pairing_v9.json` (for `aiosendspin==9.1.1`) in `/data`. **Do not commit these files or pairing tokens to GitHub**. This revision does not change the encrypted Sendspin client API/pairing flow that was used in 0.3.4; compatibility still depends on the Music Assistant server version.

## Lighting latency and limits

- MQTT publishes about 10 times per second. Home Assistant script invocation and WLED/Govee updates can be much slower than raw audio delivery. `beat_phase` is telemetry, not synchronized execution across devices.
- The sample-based tempo tracker is designed to be conservative; a valid nonzero BPM can take a few seconds to settle. Downbeats and tempo aliases cannot be guaranteed from mono audio.
- Drop recognition requires a recent buildup and short low-energy break, and may miss drops that don't fit this pattern.
- Use brightness/mode cooldowns and avoid changing a light effect on every onset. The example automation has `light_reaction_aggression` levels 1–4 and assumes an additional Date+Time helper named `input_datetime.wled_gledopto_last_reaction`.

## Development tests

```bash
PYTHONPATH=govee_audio_analyzer python -m unittest discover -s tests -v
```

No external Sendspin or MQTT server is required for the tests. They do **not** prove that the add-on builds or pairs on your real HA environment.


## v0.4.1 kick event output

For each newly detected `kick_candidate`, publish JSON to the non-retained
MQTT topic `reactive_lights/events/kick` with event, count, strength, and
timestamp. The regular `govee_audio/state` messages and discovery are unchanged.

## v0.4.2 kick events and diagnostic logging

Fast kick detector analyzes PCM every ~25 ms (75 ms minimum between accepted kicks), separately from original 100-ms audio metrics. Sends JSON to `reactive_lights/events/kick`: `event`, `count`, `strength`, `timestamp`. MQTT events are non-retained. It does not alter the `govee_audio/state` legacy kick metrics.

Configure `log_level: DEBUG` for per-kick logs and 10-second health checks. Each 60 seconds of active Sendspin streaming logs a long INFO report with fast kick count, other percussive/onset/buildup/drop counts and audio statistics. Logs also summarize each stream at the end. `kick_sensitivity` is between `0.0` and `1.0`, default `0.55`; restart add-on after change.
