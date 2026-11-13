# Govee Audio Analyzer — Home Assistant add-on repository

This repository contains a community Home Assistant add-on receiving direct PCM audio over Music Assistant **Sendspin**, analyzing it, and publishing MQTT discovery entities for Home Assistant. **No microphone is required.**

## Install / upgrade

1. Back up your running add-on, automation, and MQTT settings. If you already have this GitHub repository installed, replace its contents with these files **at the repository root** and push the changes. Keep `repository.yaml` at the root and `govee_audio_analyzer/` as a subdirectory.
2. In Home Assistant, refresh the app/add-on store, select **Govee Sendspin Audio Analyzer**, and update to **0.4.0**. If it does not appear, check that the repository URL is added and refresh repository metadata.
3. Keep your `sendspin_server` and MQTT settings. This upgrade does not change Sendspin identity/pairing files in `/data`.
4. Restart the app. Confirm it connects to Music Assistant and the `Govee Audio Analyzer` Sendspin player is receiving audio.
5. Check newly discovered MQTT sensors under the existing device. Existing nine MQTT keys and unique IDs are preserved, though **`energy` reacts faster** and the band amplitudes have corrected normalization; review thresholds.
6. Optionally copy `examples/govee_smart_music_dj_upstairs_v3.yaml` into your Home Assistant script editor. This is a separate example, **not automatically installed** by the add-on; its entity IDs and helpers need to match your system.

## What's new in 0.4.0

- Retains original nine keys: `energy`, `rms`, `bass`, `mid`, `treble`, `trend`, `onset`, `buildup`, `drop`.
- Corrects FFT window/Parseval normalization. The original `bass`, `mid`, `treble` remain relative shares (NOT intensity percentages).
- Adds independently adaptive sub-bass/bass/mid/treble energy, transient RMS, peak amplitude, spectral descriptors, onset strength and density, dynamic range estimate, BPM, beat phase/confidence/stability, percussion *candidates*, and heuristic buildup/drop scores.
- Adds MQTT discovery for each new metric and `expire_after` for binary sensors so stale event flags do not remain indefinitely after a stream stops.
- Retains the existing Sendspin receiver, PCM requirements, and `aiosendspin==9.1.1` pairing protocol.

See [`govee_audio_analyzer/DOCS.md`](govee_audio_analyzer/DOCS.md) for details and caveats.

## Verification

Run from the repository root with NumPy and Python installed:

```bash
PYTHONPATH=govee_audio_analyzer python -m unittest discover -s tests -v
```

Synthetic PCM tests, simulated Sendspin callbacks, and mocked MQTT discovery were exercised. **Live Home Assistant/Music Assistant pairing, Docker build, actual broker delivery, and physical lights have not been validated.** Music-event classifiers are heuristics, not ML instrument transcription. Test the effect thresholds with your speakers before depending on them.

## Security

Never commit MQTT passwords, Sendspin pairing tokens, state stored in `/data`, or other secrets. Configure credentials through the add-on Configuration tab. The add-on may print a pairing token to its private logs; treat it as a secret.


## v0.4.1: realtime MQTT kick events

This version retains the v0.4.0 sensor and Sendspin behavior and additionally
publishes a separate MQTT message each time the existing analyzer confirms a
new `kick_candidate`. The topic is `reactive_lights/events/kick` (override using
the `KICK_EVENT_TOPIC` environment variable). The messages are **not retained**.

Example message:

```json
{"event":"kick","count":42,"strength":0.76,"timestamp":1794539202.125}
```

The event publisher runs in the real Sendspin audio callback and also in the
standalone FFT/FFmpeg path. It reuses the existing connected MQTT client and
preserves `govee_audio/state` and Home Assistant discovery. No Home Assistant
entity is created for this event topic; use MQTT **Listen to a topic** to watch it.

**Note:** This update exports kick events, it does not change the v0.4.0 kick
detection algorithm. A detected candidate may still be a false positive, and
undetected kicks cannot be sent. Your Home Assistant dynamic kick controller
should subscribe to `reactive_lights/events/kick` and handle strength filtering.

### Deployment

1. Install/update this ZIP as your local add-on repository files under the
   Home Assistant `addons` directory (or wherever your local repository lives).
2. In Home Assistant, refresh/reload the local add-on store if necessary, then
   **Rebuild** the Govee Sendspin Audio Analyzer add-on so Python source changes
   enter the image; start it again. Existing add-on settings remain unchanged.
3. Listen to `reactive_lights/events/kick` using MQTT integration tools and
   play music through the paired Sendspin player.
4. Verify that the dynamic kick automation subscribes to the same topic.
   Disable your main DJ's brightness-only actions before testing pulses.

A controller that subscribes to the topic will not receive events until the
Python process detects a kick and publishes a message.

## v0.4.2: faster kick events and playback diagnostics

- Retains the v0.4.0/v0.4.1 100-ms analyzer (legacy MQTT `govee_audio/state`, discovery IDs, and BPM unchanged).
- Adds an independent ~25-ms low-frequency attack detector for `reactive_lights/events/kick`; minimum accepted kick interval **75 ms** (audio-clock time).
- Uses low-band spectral attack, rolling median/MAD threshold, low-frequency dominance, and noise floor checks. This is heuristic and can still miss or falsely classify kicks; `kick_sensitivity` can be changed in add-on Configuration followed by restarting the add-on.
- Existing `kick_candidate`/`kick_strength` fields on `govee_audio/state` are produced by the *legacy* detector; they are not the new fast event stream. Subscribe to `reactive_lights/events/kick` for the fast events.
- Adds per-60-second active-stream summaries and a summary at stream stop/clear, reporting detected fast kicks, legacy kicks, snares, hi-hats, onsets, beats, buildup episodes (rising edges), drops, event rates, kick strength and inter-kick intervals, means/medians/min/max/std dev for energy, bass, treble, band energy, onset strength, BPM, beat confidence, etc.
- Sets `log_level: DEBUG` by default; detailed per-kick publication and ten-second detector diagnostics appear in the add-on **Log** tab. The 60-second summaries log at INFO level.
- Statistics reflect received analyzer audio while a Sendspin stream is active, not a guaranteed Music Assistant track boundary. Stream remains active during silence, so stream reports may include silence if the player sends it.
- No microphone or saved raw audio is used.

**Security:** Sendspin currently prints a private pairing token at startup for pairing. Do not share unredacted logs publicly.

**Upgrade:** Replace files in the existing `/addons` repository, refresh Add-on Store, rebuild the add-on, and restart it. Add-on configuration now exposes `log_level` and `kick_sensitivity`; if the add-on retains old options, add them in the Configuration tab (`DEBUG` and `0.55`). Listen to `reactive_lights/events/#` in MQTT to verify kick events. Ensure the Home Assistant kick automation is enabled and its REST WLED brightness command works separately.
