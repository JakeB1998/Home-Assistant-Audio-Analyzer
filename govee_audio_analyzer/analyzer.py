#!/usr/bin/env python3
"""Live PCM/FFmpeg audio -> MQTT + Home Assistant discovery.

Drop-in compatible with the original nine JSON keys and MQTT discovery IDs.
Advanced outputs are *estimates* (not guaranteed music transcription).
Input: 16-bit signed mono PCM at 22050 Hz from FFmpeg, 100-ms hops.
"""
import json
import os
import subprocess
import sys
import time
from collections import deque

import numpy as np

SAMPLE_RATE = 22050
HOP = 2205                       # 100 ms
FFT_SIZE = 8192                 # 372 ms: frequency bands
TRANSIENT_SIZE = 3072           # 139 ms, overlaps 100-ms hops (no missing samples)
TOPIC = os.getenv("MQTT_TOPIC", "govee_audio/state")
AVAIL = os.getenv("MQTT_AVAILABILITY_TOPIC", "govee_audio/availability")
KICK_EVENT_TOPIC = os.getenv("KICK_EVENT_TOPIC", "reactive_lights/events/kick")
INPUT = os.getenv("AUDIO_INPUT", "")
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
EPS = 1e-12

# Preserve the original metric names exactly. Extra keys are additive.
EXISTING = {"energy", "rms", "bass", "mid", "treble", "trend", "onset", "buildup", "drop"}
SENSORS = {
    "energy": "Energy", "rms": "RMS", "bass": "Bass", "mid": "Mid", "treble": "Treble", "trend": "Energy Trend",
    "peak_amplitude": "Peak Amplitude", "instant_rms": "Instant RMS", "sub_bass_energy": "Sub Bass Energy",
    "bass_energy": "Bass Energy", "mid_energy": "Mid Energy", "treble_energy": "Treble Energy",
    "spectral_flux": "Spectral Flux", "spectral_centroid_hz": "Spectral Centroid",
    "spectral_rolloff_hz": "Spectral Rolloff", "spectral_flatness": "Spectral Flatness",
    "bpm": "BPM", "beat_confidence": "Beat Confidence", "beat_phase": "Beat Phase",
    "tempo_stability": "Tempo Stability", "onset_strength": "Onset Strength",
    "kick_strength": "Kick Strength", "snare_strength": "Snare Strength", "hihat_strength": "Hi Hat Strength",
    "dynamic_range_db": "Dynamic Range", "onset_density": "Onset Density",
    "buildup_score": "Buildup Score", "drop_score": "Drop Score",
}
BINARY = {
    "onset": "Onset", "buildup": "Buildup", "drop": "Drop",
    "beat": "Beat", "kick_candidate": "Kick Candidate",
    "snare_candidate": "Snare Candidate", "hihat_candidate": "Hi Hat Candidate",
}
UNITS = {"spectral_centroid_hz": "Hz", "spectral_rolloff_hz": "Hz", "bpm": "BPM", "dynamic_range_db": "dB"}


def _windowed_spectrum(frame):
    """One-sided power spectrum with correct windowed RMS energy normalization."""
    a = np.asarray(frame, dtype=np.float64)
    win = np.hanning(a.size)
    fft = np.fft.rfft(a * win)
    # Parseval: RMS^2 = sum(one-sided FFT magnitude squared) / N / sum(window^2)
    p = np.abs(fft) ** 2
    if p.size > 2:
        p[1:-1] *= 2.0
    p /= max(a.size * float(np.sum(win * win)), EPS)
    f = np.fft.rfftfreq(a.size, 1.0 / SAMPLE_RATE)
    return f, p


def _band_rms(freqs, power, low, high):
    return float(np.sqrt(max(float(power[(freqs >= low) & (freqs < high)].sum()), 0.0)))


def _clamp(value):
    return float(np.clip(value, 0.0, 1.0))


class Analyzer:
    def __init__(self):
        self.samples = np.zeros(FFT_SIZE, dtype=np.float32)
        self.sample_count = 0
        self.prev_short_mag = None
        self.prev_short_bands = None
        self.flux_history = deque(maxlen=50)
        self.onset_envelope = deque(maxlen=160)  # 16 seconds @ 10 Hz
        self.onset_times = deque(maxlen=160)
        self.level_history = deque(maxlen=160)
        self.band_reference = {}     # Adaptive per-band peaks
        self.last_onset = -100.0
        self.last_kick = -100.0
        self.last_snare = -100.0
        self.last_hihat = -100.0
        self.last_drop = -100.0
        self.last_buildup = -100.0
        self.last_quiet_after_buildup = -100.0
        self.previous_energy = 0.0
        self.peak = 0.08
        self.floor = 0.002
        self.bpm = 0.0
        self.beat_confidence = 0.0
        self.tempo_stability = 0.0
        self.beat_anchor = None
        self.previous_beat_index = None
        self.tempo_history = deque(maxlen=12)
        self.last_tempo_update = -100.0

    def _relative_band(self, key, level):
        """Independent adaptive 0..1 intensity, not a ratio of all bands."""
        ref = self.band_reference.get(key, max(level, 0.005))
        ref = max(level, ref * 0.998, 0.0005)
        self.band_reference[key] = ref
        # No fake 'full intensity' when everything is essentially silent.
        if level < 0.0006:
            return 0.0
        return _clamp(level / max(ref, 0.005))

    def _update_tempo(self, t):
        if t - self.last_tempo_update < 0.5 or len(self.onset_envelope) < 60:
            return
        self.last_tempo_update = t
        a = np.asarray(self.onset_envelope, dtype=np.float64)
        if np.count_nonzero(a > 0.03) < 7:
            self.beat_confidence *= 0.85
            if self.beat_confidence < 0.15:
                self.bpm, self.beat_anchor = 0.0, None
            return
        # Lag range 3..10 hops => 60..200 BPM (10 Hz envelope).
        # Localized normalized autocorrelation; not a proven metrical interpretation.
        results = []
        a = a - np.median(a)
        a = np.maximum(a, 0.0)
        for lag in range(3, 11):
            left, right = a[:-lag], a[lag:]
            denom = float(np.linalg.norm(left) * np.linalg.norm(right))
            correlation = float(np.dot(left, right) / denom) if denom > EPS else 0.0
            # Prefer the tracked tempo when nearly tied to reduce double/half-time hopping.
            bpm = 600.0 / lag
            bonus = 0.06 if self.bpm and abs(bpm - self.bpm) < 5 else 0.0
            results.append((correlation + bonus, correlation, bpm))
        results.sort(reverse=True)
        _, score, bpm = results[0]
        # An FFT-of-onsets at 10 Hz only provides coarse BPM bins (e.g. 120 or
        # 150). Refine from the average interval between clearly repeated
        # attacks. This also corrects autocorrelation's 1/2 and 1/3 tempo aliases
        # on clean click tracks. For irregular subdivisions, keep the ACF result.
        times = np.asarray(self.onset_times, dtype=float)
        if times.size >= 10:
            intervals = np.diff(times[-26:])
            recent_intervals = intervals[(intervals >= 0.26) & (intervals <= 1.0)]
            if recent_intervals.size >= 8:
                mean_interval = float(recent_intervals.mean())
                variation = float(recent_intervals.std() / mean_interval)
                if variation < 0.19:
                    refined_bpm = 60.0 / mean_interval
                    if 60 <= refined_bpm <= 200:
                        bpm = refined_bpm
                        # An evenly spaced series of onsets supports a tempo
                        # hypothesis, but cannot prove musical downbeat placement.
                        score = max(score, _clamp(1.0 - variation * 2.0) * 0.72)
        if score < 0.27:
            self.beat_confidence = max(0.0, self.beat_confidence - 0.08)
            return
        self.tempo_history.append(bpm)
        # Stability is the proportion of recent estimates within 6 BPM of modal estimate.
        stability = sum(abs(x - bpm) <= 6 for x in self.tempo_history) / len(self.tempo_history)
        self.tempo_stability = stability
        self.beat_confidence = _clamp(score * stability)
        if self.beat_confidence >= 0.28:
            previous_bpm = self.bpm
            self.bpm = bpm
            if self.beat_anchor is None or (previous_bpm and abs(previous_bpm - bpm) > 8):
                self.beat_anchor = self.onset_times[-1] if self.onset_times else t
                self.previous_beat_index = None

    def process(self, new_samples):
        block = np.asarray(new_samples, dtype=np.float32)
        if block.shape != (HOP,):
            raise ValueError(f"Expected {HOP} mono samples, got {block.shape}")
        # Audio sample clock is robust for file tests; monotonic wall time is not.
        self.sample_count += HOP
        t = self.sample_count / SAMPLE_RATE
        self.samples[:-HOP] = self.samples[HOP:]
        self.samples[-HOP:] = block
        long_freq, long_power = _windowed_spectrum(self.samples)
        short_freq, short_power = _windowed_spectrum(self.samples[-TRANSIENT_SIZE:])
        raw_rms = float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))
        window_rms = float(np.sqrt(np.mean(self.samples.astype(np.float64) ** 2)))
        raw_peak = float(np.max(np.abs(block)))
        # A slowly decaying level peak; avoid rising to 1 from numerical background noise.
        self.peak = max(raw_rms, self.peak * 0.999)
        self.floor = min(self.peak * 0.15, max(0.001, self.floor * 0.999 + raw_rms * 0.001))
        energy = _clamp((raw_rms - self.floor) / max(self.peak - self.floor, 0.01))
        if raw_rms < 0.0015:
            energy = 0.0
        # Legacy frequency ratios, now with window-normalized RMS of real bands.
        bass = _band_rms(long_freq, long_power, 30, 160)
        mids = _band_rms(long_freq, long_power, 160, 2500)
        treble = _band_rms(long_freq, long_power, 2500, 9000)
        band_total = bass + mids + treble
        if band_total < 1e-5:
            bass_ratio = mid_ratio = treble_ratio = 0.0
        else:
            bass_ratio, mid_ratio, treble_ratio = (bass / band_total, mids / band_total, treble / band_total)
        sub = _band_rms(long_freq, long_power, 20, 60)
        band_levels = {
            "sub_bass_energy": self._relative_band("sub", sub),
            "bass_energy": self._relative_band("bass", bass),
            "mid_energy": self._relative_band("mid", mids),
            "treble_energy": self._relative_band("treble", treble),
        }
        # Spectral descriptors weighted by *power*, in the useful audible region.
        usable = (long_freq >= 30) & (long_freq <= 9000)
        f, p = long_freq[usable], long_power[usable]
        if p.sum() > 1e-8 and raw_rms >= 0.0015:
            centroid = float(np.sum(f * p) / np.sum(p))
            roll_index = min(int(np.searchsorted(np.cumsum(p), 0.85 * float(p.sum()))), len(f) - 1)
            rolloff = float(f[roll_index])
            flatness = float(np.exp(np.mean(np.log(p + 1e-12))) / (np.mean(p) + 1e-12))
        else:
            centroid = rolloff = flatness = 0.0
        short_mag = np.sqrt(np.maximum(short_power, 0.0))
        # A flux based on raw magnitude (captures attacks) plus shape changes.
        flux = 0.0
        if self.prev_short_mag is not None:
            raw_change = float(np.maximum(short_mag - self.prev_short_mag, 0).sum())
            flux = raw_change / max(float(self.prev_short_mag.sum()), 0.02)
        self.prev_short_mag = short_mag
        history = np.asarray(self.flux_history, dtype=np.float64)
        baseline = float(np.median(history)) if len(history) >= 6 else 0.0
        mad = float(np.median(np.abs(history - baseline))) if len(history) >= 6 else 0.0
        threshold = max(0.15, baseline + 2.5 * max(mad, 0.025))
        onset_strength = _clamp(flux / max(threshold * 3, 0.001))
        onset = bool(raw_rms > 0.003 and flux > threshold and t - self.last_onset >= 0.18)
        self.flux_history.append(flux)
        if onset:
            self.last_onset = t
            self.onset_times.append(t)
        self.onset_envelope.append(min(flux, 3.0) if onset else 0.0)
        self._update_tempo(t)
        # Short-window band attacks for *candidate* percussion (not ML classification).
        short_bands = np.array([
            _band_rms(short_freq, short_power, 35, 180),
            _band_rms(short_freq, short_power, 180, 1800),
            _band_rms(short_freq, short_power, 1800, 6500),
            _band_rms(short_freq, short_power, 6500, 10000),
        ])
        if self.prev_short_bands is None:
            changes = np.zeros(4)
        else:
            changes = np.maximum(short_bands - self.prev_short_bands, 0)
        self.prev_short_bands = short_bands
        attack_total = float(changes.sum()) + EPS
        kick_strength = _clamp(changes[0] / attack_total) if onset else 0.0
        snare_strength = _clamp((changes[1] + changes[2]) / attack_total) if onset else 0.0
        hihat_strength = _clamp(changes[3] / attack_total) if onset else 0.0
        kick = bool(onset and kick_strength > 0.42 and changes[0] > 0.006 and t - self.last_kick > 0.18)
        snare = bool(onset and snare_strength > 0.52 and (changes[1] + changes[2]) > 0.008 and t - self.last_snare > 0.18)
        hihat = bool(onset and hihat_strength > 0.38 and changes[3] > 0.004 and t - self.last_hihat > 0.12)
        if kick: self.last_kick = t
        if snare: self.last_snare = t
        if hihat: self.last_hihat = t
        # Gentle beat anchor correction only for onsets close to predicted beat.
        beat = False
        phase = 0.0
        if self.bpm and self.beat_confidence >= 0.28 and self.beat_anchor is not None:
            period = 60 / self.bpm
            if onset:
                cycles = round((t - self.beat_anchor) / period)
                error = t - (self.beat_anchor + cycles * period)
                if abs(error) < 0.16 * period:
                    self.beat_anchor += 0.12 * error
            beats_elapsed = (t - self.beat_anchor) / period
            phase = beats_elapsed % 1.0
            index = int(np.floor(beats_elapsed))
            if self.previous_beat_index is None:
                self.previous_beat_index = index
            elif index > self.previous_beat_index:
                beat = True
                self.previous_beat_index = index
        # 15-second trend and transient density.
        self.level_history.append(energy)
        h = np.asarray(self.level_history, dtype=np.float64)
        short_avg = float(h[-15:].mean())
        old_avg = float(h[-50:-25].mean()) if len(h) >= 50 else short_avg
        delta = short_avg - old_avg
        trend = "rising" if delta > 0.13 else "falling" if delta < -0.13 else "steady"
        onset_density = sum(t - x <= 4.0 for x in self.onset_times) / 4.0
        buildup_score = _clamp(max(delta, 0) / 0.30 * min(onset_density / 2.0, 1.0))
        buildup = bool(len(h) >= 50 and buildup_score > 0.48 and short_avg > 0.38)
        if buildup:
            self.last_buildup = t
        # Conservative drop *candidate*: a recent buildup, a brief low-energy
        # break, then a strong new onset. This intentionally misses some drops
        # without breaks rather than firing on every loud kick in a buildup.
        if t - self.last_buildup <= 5.0 and energy < 0.40:
            self.last_quiet_after_buildup = t
        energy_jump = max(0.0, energy - self.previous_energy)
        drop_score = _clamp(0.5 * onset_strength + 0.5 * energy_jump / 0.5)
        drop = bool(onset and t - self.last_buildup <= 5.0
                    and 0.0 < t - self.last_quiet_after_buildup <= 1.5
                    and energy > 0.70 and energy_jump > 0.25
                    and drop_score > 0.60 and t - self.last_drop > 5.0)
        if drop: self.last_drop = t
        self.previous_energy = energy
        nonzero_levels = np.asarray([x for x in h[-50:] if x > 0.015])
        dynamic_range_db = (float(20 * np.log10(np.percentile(nonzero_levels, 90) /
                         max(np.percentile(nonzero_levels, 10), 1e-4)))
                         if len(nonzero_levels) >= 15 else 0.0)
        return {
            "energy": round(energy, 3), "rms": round(window_rms, 5),
            "instant_rms": round(raw_rms, 5),
            "bass": round(bass_ratio, 3), "mid": round(mid_ratio, 3), "treble": round(treble_ratio, 3),
            "trend": trend, "onset": onset, "buildup": buildup, "drop": drop,
            "peak_amplitude": round(raw_peak, 4),
            **{k: round(v, 3) for k, v in band_levels.items()},
            "spectral_flux": round(flux, 4), "spectral_centroid_hz": round(centroid, 1),
            "spectral_rolloff_hz": round(rolloff, 1), "spectral_flatness": round(flatness, 4),
            "onset_strength": round(onset_strength, 3), "onset_density": round(onset_density, 2),
            "kick_candidate": kick, "snare_candidate": snare, "hihat_candidate": hihat,
            "kick_strength": round(kick_strength, 3), "snare_strength": round(snare_strength, 3),
            "hihat_strength": round(hihat_strength, 3),
            "bpm": round(self.bpm, 1), "beat_confidence": round(self.beat_confidence, 3),
            "beat_phase": round(phase, 3), "beat": beat, "tempo_stability": round(self.tempo_stability, 3),
            "dynamic_range_db": round(dynamic_range_db, 1),
            "buildup_score": round(buildup_score, 3), "drop_score": round(drop_score, 3),
        }



class KickEventPublisher:
    """Publish one non-retained MQTT event per new kick_candidate onset.

    Call once per processed analysis hop. The detector itself already has a
    refractory period; rising-edge detection adds protection against duplicate
    frames while retaining every newly accepted event.
    """

    def __init__(self):
        self.previous_candidate = False
        self.event_count = 0

    def publish_event(self, mqtt_client, strength):
        """Publish one already-confirmed fast kick. No rising-edge gate here."""
        if mqtt_client is None:
            return False
        self.event_count += 1
        event = {
            "event": "kick", "count": self.event_count,
            "strength": round(float(strength), 3), "timestamp": time.time(),
        }
        mqtt_client.publish(KICK_EVENT_TOPIC, json.dumps(event), qos=0, retain=False)
        return True

    def process(self, metrics, mqtt_client):
        candidate = metrics.get("kick_candidate") is True
        is_new = candidate and not self.previous_candidate
        self.previous_candidate = candidate
        if not is_new or mqtt_client is None:
            return False
        self.event_count += 1
        event = {
            "event": "kick",
            "count": self.event_count,
            "strength": float(metrics.get("kick_strength", 0.0)),
            "timestamp": time.time(),
        }
        mqtt_client.publish(KICK_EVENT_TOPIC, json.dumps(event), qos=0, retain=False)
        return True

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
    for key, name in SENSORS.items():
        config = {"name": f"Audio {name}", "unique_id": "govee_audio_" + key,
                  "state_topic": TOPIC, "value_template": "{{ value_json." + key + " }}",
                  "availability_topic": AVAIL, "expire_after": 5, "device": device}
        if key in UNITS: config["unit_of_measurement"] = UNITS[key]
        client.publish(f"homeassistant/sensor/govee_audio_{key}/config", json.dumps(config), retain=True)
    for key, name in BINARY.items():
        config = {"name": f"Audio {name}", "unique_id": "govee_audio_" + key,
                  "state_topic": TOPIC,
                  "value_template": "{{ 'ON' if value_json." + key + " else 'OFF' }}",
                  "payload_on": "ON", "payload_off": "OFF", "availability_topic": AVAIL,
                  "expire_after": 5, "device": device}
        client.publish(f"homeassistant/binary_sensor/govee_audio_{key}/config", json.dumps(config), retain=True)
    client.publish(AVAIL, "online", retain=True)
    return client


def main():
    if not INPUT:
        raise SystemExit("Set AUDIO_INPUT to an FFmpeg-readable URL/file/device, or pipe:0 for PCM stdin")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin"]
    if INPUT == "pipe:0":
        cmd += ["-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1"]
    cmd += ["-i", INPUT, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1"]
    if INPUT == "pipe:0":
        cmd.remove("-nostdin")
    proc = subprocess.Popen(cmd, stdin=None if INPUT == "pipe:0" else subprocess.DEVNULL,
                            stdout=subprocess.PIPE)
    client = None if DRY_RUN else connect_mqtt()
    analyzer = Analyzer()
    kick_events = KickEventPublisher()
    try:
        while True:
            block = proc.stdout.read(HOP * 2)
            if len(block) < HOP * 2:
                break
            samples = np.frombuffer(block, dtype="<i2").astype(np.float32) / 32768.0
            result = analyzer.process(samples)
            if client:
                client.publish(TOPIC, json.dumps(result), retain=False)
                kick_events.process(result, client)
            else:
                print(json.dumps(result), flush=True)
    finally:
        if client:
            client.publish(AVAIL, "offline", retain=True)
            client.loop_stop()
            client.disconnect()
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

if __name__ == "__main__":
    main()
