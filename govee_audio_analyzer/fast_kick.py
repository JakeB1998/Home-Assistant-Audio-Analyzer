"""Fast, conservative kick-like transient detector for a mono PCM music mix.

A 25 ms analysis hop locates low-frequency spectral attacks. Instead of firing
immediately, a 75 ms observation period checks that the waveform energy decays
quickly, as expected for many kick drums. This is a signal-processing heuristic,
not instrument recognition: certain plucked bass sounds can still resemble kicks.
"""
from collections import deque
import os
import numpy as np

RATE = 22050
FAST_HOP = 551  # ~25 ms
WINDOW = 2048
MIN_INTERVAL = 0.075  # minimum separation between *accepted onsets*
CONFIRM_HOPS = 3  # ~75 ms post-onset verification latency


def _clamp(value, lo, hi):
    return min(hi, max(lo, float(value)))


class FastKickDetector:
    def __init__(self, sensitivity=None, decay_ratio_max=None):
        self.sensitivity = _clamp(os.getenv('KICK_SENSITIVITY', '0.55') if sensitivity is None else sensitivity, 0, 1)
        self.decay_ratio_max = _clamp(os.getenv('KICK_DECAY_RATIO_MAX', '0.50') if decay_ratio_max is None else decay_ratio_max, 0.15, 0.95)
        self.audio = np.zeros(WINDOW, dtype=np.float64)
        self.samples = 0
        self.last_kick = -100.0
        self.last_low = 0.0
        self.last_spectrum = None
        self.low_history = deque(maxlen=80)
        self.attack_history = deque(maxlen=80)
        self.pending = None
        self.armed = True
        self.candidates = 0
        self.accepted = 0
        self.rejected_refractory = 0
        self.rejected_threshold = 0
        self.rejected_decay = 0
        self.rejected_subbass = 0
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
        block_rms = float(np.sqrt(np.mean(block * block)))
        spec = np.abs(np.fft.rfft(self.audio * self.window)) / (WINDOW / 2)
        low = float(np.linalg.norm(spec[self.low_mask]))
        mid = float(np.linalg.norm(spec[self.mid_mask]))
        sub = float(np.linalg.norm(spec[self.sub_mask]))
        if self.last_spectrum is None:
            self.last_spectrum = spec
            self.last_low = low
            self.low_history.append(low)
            return None

        pos = np.maximum(spec[self.low_mask] - self.last_spectrum[self.low_mask], 0.0)
        spectral_attack = float(np.linalg.norm(pos))
        attack = max(spectral_attack, max(0.0, low - self.last_low))
        self.last_spectrum = spec
        self.last_low = low
        hist = np.asarray(self.attack_history, dtype=np.float64)
        baseline = float(np.median(hist)) if len(hist) >= 12 else 0.0
        mad = float(np.median(np.abs(hist - baseline))) if len(hist) >= 12 else 0.0
        low_hist = np.asarray(self.low_history, dtype=np.float64)
        reference = float(np.median(low_hist)) if len(low_hist) >= 12 else low
        threshold = max(0.001, baseline + (2.5 - 1.5 * self.sensitivity) * max(mad, 0.0005))
        relative = attack / max(reference, 0.003)
        presence = low / max(low + mid, 1e-9)
        sub_ratio = sub / max(low, 1e-9)
        # Saturating relative strength avoids almost everything scoring 1.0
        # whenever the recent baseline is quiet.
        attack_quality = 1 - np.exp(-min(relative, 20.0) / 1.5)
        strength = _clamp(0.45 * attack_quality + 0.35 * presence + 0.20 * sub_ratio, 0, 1)
        self.max_strength = max(self.max_strength, strength)
        self.attack_history.append(attack)
        self.low_history.append(low)

        # Once an attack is queued, don't start another detection in its tail.
        # A second event can be considered after the pending window finishes.
        if self.pending is not None:
            pending = self.pending
            pending['age'] += 1
            if pending['age'] >= CONFIRM_HOPS:
                self.pending = None
                decay_ratio = block_rms / max(pending['peak_rms'], 0.0001)
                if decay_ratio > self.decay_ratio_max:
                    self.rejected_decay += 1
                    return None
                if pending['t_audio'] - self.last_kick < MIN_INTERVAL - 0.0001:
                    self.rejected_refractory += 1
                    return None
                self.last_kick = pending['t_audio']
                self.accepted += 1
                return {
                    'strength': round(pending['strength'], 3),
                    'relative_attack': round(pending['relative'], 3),
                    'spectral_attack': round(pending['spectral_attack'], 5),
                    'bass_presence': round(pending['presence'], 3),
                    'sub_ratio': round(pending['sub_ratio'], 3),
                    'decay_ratio': round(decay_ratio, 3),
                    'threshold': round(pending['threshold'], 5),
                    't_audio': round(pending['t_audio'], 3),
                }
            return None

        # Re-arm only when the spectrum settles; avoids retriggering on a long
        # low note or on the secondary bump of one kick's decaying waveform.
        if not self.armed:
            if attack < max(threshold, 0.007) and relative < 0.24:
                self.armed = True
            else:
                return None

        candidate = (block_rms >= 0.008 and low >= 0.004 and attack >= threshold and
                     relative >= (0.25 - 0.10 * self.sensitivity) and
                     presence >= 0.42 and strength >= (0.40 - 0.08 * self.sensitivity))
        if not candidate:
            self.rejected_threshold += 1
            return None
        if sub_ratio < 0.42:
            self.rejected_subbass += 1
            return None
        self.candidates += 1
        self.armed = False
        self.pending = {
            'age': 0, 'peak_rms': block_rms, 't_audio': now,
            'strength': strength, 'relative': relative,
            'spectral_attack': spectral_attack, 'presence': presence,
            'sub_ratio': sub_ratio, 'threshold': threshold,
        }
        return None
