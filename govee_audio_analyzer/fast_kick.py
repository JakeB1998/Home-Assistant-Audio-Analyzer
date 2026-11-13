"""25-ms low-frequency onset detector. Kept separate from the 100-ms legacy analyzer.

Heuristic, not a trained kick classifier. Uses low-band spectral changes,
relative strength compared with recent audio, and a 75-ms refractory period.
"""
from collections import deque
import os
import numpy as np

RATE = 22050
FAST_HOP = 551  # ~24.99 ms; allows events separated by 75 ms
WINDOW = 2048  # ~93 ms for low-frequency discrimination
MIN_INTERVAL = 0.075


class FastKickDetector:
    def __init__(self, sensitivity=None):
        value = os.getenv('KICK_SENSITIVITY', '0.55') if sensitivity is None else sensitivity
        self.sensitivity = min(1., max(0., float(value)))
        self.audio = np.zeros(WINDOW, dtype=np.float64)
        self.samples = 0
        self.last_kick = -100.0
        self.last_low = 0.0
        self.last_spectrum = None
        self.low_history = deque(maxlen=80)
        self.attack_history = deque(maxlen=80)
        self.candidates = 0
        self.accepted = 0
        self.rejected_refractory = 0
        self.rejected_threshold = 0
        self.max_strength = 0.0
        freqs = np.fft.rfftfreq(WINDOW, 1 / RATE)
        self.low_mask = (freqs >= 35) & (freqs < 180)
        self.sub_mask = (freqs >= 35) & (freqs < 95)
        self.mid_mask = (freqs >= 180) & (freqs < 2500)
        self.window = np.hanning(WINDOW)

    def process(self, samples):
        block = np.asarray(samples, dtype=np.float64)
        if block.shape != (FAST_HOP,):
            raise ValueError(f'Expected {FAST_HOP} samples, got {block.shape}')
        self.audio[:-FAST_HOP] = self.audio[FAST_HOP:]
        self.audio[-FAST_HOP:] = block
        self.samples += FAST_HOP
        if self.samples < WINDOW:
            return None
        now = self.samples / RATE
        spec = np.abs(np.fft.rfft(self.audio * self.window)) / (WINDOW / 2)
        low = float(np.linalg.norm(spec[self.low_mask]))
        mid = float(np.linalg.norm(spec[self.mid_mask]))
        sub = float(np.linalg.norm(spec[self.sub_mask]))
        if self.last_spectrum is None:
            self.last_spectrum = spec
            self.last_low = low
            self.low_history.append(low)
            return None
        # Detect a rising low-frequency spectrum, not sustained bass amplitude.
        positive = np.maximum(spec[self.low_mask] - self.last_spectrum[self.low_mask], 0.0)
        spectral_attack = float(np.linalg.norm(positive))
        envelope_attack = max(0., low - self.last_low)
        attack = max(spectral_attack, envelope_attack)
        self.last_spectrum = spec
        self.last_low = low
        hist = np.asarray(self.attack_history, dtype=np.float64)
        baseline = float(np.median(hist)) if len(hist) >= 12 else 0.0
        mad = float(np.median(np.abs(hist - baseline))) if len(hist) >= 12 else 0.0
        low_hist = np.asarray(self.low_history, dtype=np.float64)
        reference = float(np.median(low_hist)) if len(low_hist) >= 12 else low
        # Greater sensitivity allows smaller transients while retaining a noise floor.
        threshold = max(0.0010, baseline + (2.5 - 1.5 * self.sensitivity) * max(mad, 0.0005))
        relative = attack / max(reference, 0.003)
        presence = low / max(low + mid, 1e-9)
        sub_ratio = sub / max(low, 1e-9)
        strength = min(1.0, max(0., 0.45 * relative + 0.35 * presence + 0.2 * sub_ratio))
        self.max_strength = max(self.max_strength, strength)
        self.attack_history.append(attack)
        self.low_history.append(low)
        # Reject silence/noise, bass-free transient and weak attacks.
        is_candidate = (low >= 0.003 and attack >= threshold and
                        relative >= 0.20 - 0.12 * self.sensitivity and
                        presence >= 0.33 and strength >= 0.25 - .12 * self.sensitivity)
        if not is_candidate:
            self.rejected_threshold += 1
            return None
        self.candidates += 1
        if now - self.last_kick < MIN_INTERVAL - 0.0001:
            self.rejected_refractory += 1
            return None
        self.last_kick = now
        self.accepted += 1
        return {'strength': round(strength, 3), 'relative_attack': round(relative, 3),
                'spectral_attack': round(spectral_attack, 5),
                'bass_presence': round(presence, 3), 'sub_ratio': round(sub_ratio, 3),
                'threshold': round(threshold, 5), 't_audio': round(now, 3)}
