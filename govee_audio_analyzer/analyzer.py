#!/usr/bin/env python3
"""Live PCM/FFmpeg audio analysis -> Home Assistant MQTT discovery.

Supports any audio source FFmpeg can read via AUDIO_INPUT. For raw PCM on stdin,
set AUDIO_INPUT=pipe:0 and pass 16-bit little-endian mono 22050 Hz samples.
A Sendspin decoder/client is NOT included; its audio must be routed to this input.
"""
import json
import math
import os
import subprocess
import sys
import time
from collections import deque

import numpy as np

SAMPLE_RATE = 22050
HOP = 2205              # 100 milliseconds
FFT_SIZE = 8192         # ~372 ms window; fine for energy, not sample-accurate timing
TOPIC = os.getenv("MQTT_TOPIC", "govee_audio/state")
AVAIL = "govee_audio/availability"
INPUT = os.getenv("AUDIO_INPUT", "")
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"


def features(frame):
    """Return uncalibrated amplitude and per-band RMS from a sample window."""
    a = np.asarray(frame, dtype=np.float32)
    rms = float(np.sqrt(np.mean(a * a)))
    spec = np.fft.rfft(a * np.hanning(len(a)))
    # RMS-compatible normalization for windowed frequency bins; relative bands.
    freqs = np.fft.rfftfreq(len(a), 1 / SAMPLE_RATE)
    p = np.abs(spec) ** 2
    def band(lo, hi):
        mask = (freqs >= lo) & (freqs < hi)
        return float(np.sqrt(p[mask].sum())) / max(len(a) / 2, 1)
    return rms, band(30, 160), band(160, 2500), band(2500, 9000), p


class Analyzer:
    def __init__(self):
        self.samples = np.zeros(FFT_SIZE, dtype=np.float32)
        self.level_history = deque(maxlen=80)  # ~8 seconds
        self.prev_spectrum = None
        self.flux_history = deque(maxlen=30)
        self.prev_energy = 0.0
        self.last_onset = 0.0
        self.last_drop = 0.0
        self.last_output = 0.0
        self.floor = 0.008
        self.peak = 0.1

    def process(self, new_samples):
        self.samples[:-HOP] = self.samples[HOP:]
        self.samples[-HOP:] = new_samples
        rms, bass, mids, treble, power = features(self.samples)
        now = time.monotonic()
        # Slow adaptive amplitude normalization; treats audio with different volume levels.
        self.peak = max(rms, self.peak * 0.999)
        self.floor = max(0.001, min(self.floor * 0.999 + rms * 0.001, self.peak * 0.15))
        normalized = max(0, min(1, (rms - self.floor) / max(self.peak - self.floor, 0.01)))
        # Band levels are relative ratios rather than calibrated absolute signal power.
        total = max(bass + mids + treble, 0.00001)
        bass_fraction = bass / total
        self.level_history.append(normalized)
        h = list(self.level_history)
        old = float(np.mean(h[-50:-25])) if len(h) >= 50 else normalized
        recent = float(np.mean(h[-15:])) if len(h) >= 15 else normalized
        delta = recent - old
        trend = 'rising' if delta > 0.13 else 'falling' if delta < -0.13 else 'steady'
        flux = 0.0
        if self.prev_spectrum is not None:
            # Positive spectral changes, normalized for changing volume.
            prior = self.prev_spectrum / max(self.prev_spectrum.sum(), 1e-12)
            current = power / max(power.sum(), 1e-12)
            flux = float(np.maximum(current - prior, 0).sum())
        self.prev_spectrum = power
        mean_flux = max(float(np.mean(self.flux_history)), 0.004) if self.flux_history else 0.004
        onset = (flux > mean_flux * 1.9 and flux > 0.035 and now - self.last_onset > 0.17)
        self.flux_history.append(flux)
        if onset:
            self.last_onset = now
        # Heuristic music event, not a guaranteed musical drop detector.
        drop = bool(onset and normalized >= 0.75 and recent > old + 0.2 and now - self.last_drop > 2.5)
        if drop:
            self.last_drop = now
        buildup = bool(trend == 'rising' and recent > 0.38)
        self.prev_energy = normalized
        return {
            "energy": round(normalized, 3),
            "rms": round(rms, 5),
            "bass": round(bass_fraction, 3),
            "mid": round(mids / total, 3),
            "treble": round(treble / total, 3),
            "trend": trend,
            "onset": bool(onset),
            "buildup": buildup,
            "drop": drop,
        }


def connect_mqtt():
    import paho.mqtt.client as mqtt
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="govee_audio_analyzer")
    user = os.getenv("MQTT_USER", "")
    if user:
        client.username_pw_set(user, os.getenv("MQTT_PASSWORD", ""))
    client.will_set(AVAIL, "offline", retain=True)
    client.connect(os.getenv("MQTT_HOST", "127.0.0.1"), int(os.getenv("MQTT_PORT", "1883")), 30)
    client.loop_start()
    device = {"identifiers": ["govee_audio_analyzer"], "name": "Govee Audio Analyzer", "manufacturer": "DIY"}
    for key, name in (("energy", "Energy"), ("rms", "RMS"), ("bass", "Bass"), ("mid", "Mid"), ("treble", "Treble"), ("trend", "Energy Trend")):
        config = {"name": f"Audio {name}", "unique_id": "govee_audio_" + key,
                  "state_topic": TOPIC, "value_template": "{{ value_json." + key + " }}",
                  "availability_topic": AVAIL, "expire_after": 5, "device": device}
        client.publish(f"homeassistant/sensor/govee_audio_{key}/config", json.dumps(config), retain=True)
    for key, name in (("onset", "Onset"), ("buildup", "Buildup"), ("drop", "Drop")):
        config = {"name": f"Audio {name}", "unique_id": "govee_audio_" + key,
                  "state_topic": TOPIC, "value_template": "{{ 'ON' if value_json." + key + " else 'OFF' }}",
                  "payload_on": "ON", "payload_off": "OFF", "availability_topic": AVAIL,
                  "device": device}
        client.publish(f"homeassistant/binary_sensor/govee_audio_{key}/config", json.dumps(config), retain=True)
    client.publish(AVAIL, "online", retain=True)
    return client


def main():
    if not INPUT:
        raise SystemExit("Set AUDIO_INPUT to an FFmpeg-readable URL/file/device, or pipe:0 for PCM stdin")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin"]
    if INPUT == "pipe:0":
        cmd += ["-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1"]
    cmd += ["-i", INPUT, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-f", "s16le", "pipe:1"]
    # FFmpeg inherits stdin only in raw pipe mode; other source modes use /dev/null.
    if INPUT == "pipe:0":
        cmd.remove("-nostdin")
    proc = subprocess.Popen(cmd, stdin=None if INPUT == "pipe:0" else subprocess.DEVNULL,
                            stdout=subprocess.PIPE)
    client = None if DRY_RUN else connect_mqtt()
    analyzer = Analyzer()
    try:
        while True:
            block = proc.stdout.read(HOP * 2)
            if len(block) < HOP * 2:
                break
            samples = np.frombuffer(block, dtype='<i2').astype(np.float32) / 32768
            result = analyzer.process(samples)
            if client:
                client.publish(TOPIC, json.dumps(result), retain=False)
            else:
                print(json.dumps(result), flush=True)
    finally:
        if client:
            client.publish(AVAIL, "offline", retain=True)
            client.loop_stop()
            client.disconnect()
        proc.terminate()

if __name__ == "__main__":
    main()
