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
