import unittest
import numpy as np
from analyzer import Analyzer, HOP, SAMPLE_RATE, EXISTING, SENSORS, BINARY


def run_audio(samples):
    analyzer = Analyzer()
    outputs = []
    for i in range(0, len(samples) - HOP + 1, HOP):
        outputs.append(analyzer.process(samples[i:i + HOP]))
    return outputs


class AnalyzerTests(unittest.TestCase):
    def test_silence(self):
        rows = run_audio(np.zeros(SAMPLE_RATE * 8, dtype=np.float32))
        self.assertEqual(set(rows[-1]), set(SENSORS) | set(BINARY))
        self.assertTrue(EXISTING <= set(rows[-1]))
        self.assertEqual(rows[-1]['energy'], 0)
        self.assertEqual(rows[-1]['bass'], 0)
        self.assertEqual(rows[-1]['bpm'], 0)
        self.assertFalse(any(r['onset'] or r['drop'] for r in rows))
        for r in rows:
            for key in SENSORS:
                if key != 'trend':
                    self.assertTrue(np.isfinite(r[key]), (key, r[key]))

    def test_tone_frequency(self):
        t = np.arange(SAMPLE_RATE * 4) / SAMPLE_RATE
        rows = run_audio((0.2 * np.sin(2 * np.pi * 85 * t)).astype(np.float32))
        self.assertGreater(rows[-1]['bass'], 0.9)
        self.assertLess(rows[-1]['mid'], 0.1)
        self.assertAlmostEqual(rows[-1]['spectral_centroid_hz'], 85, delta=8)
        self.assertGreater(rows[-1]['rms'], 0.12)
        self.assertLess(rows[-1]['spectral_flatness'], 0.1)

    def test_treble(self):
        t = np.arange(SAMPLE_RATE * 4) / SAMPLE_RATE
        rows = run_audio((0.2 * np.sin(2 * np.pi * 4000 * t)).astype(np.float32))
        self.assertGreater(rows[-1]['treble'], 0.9)
        self.assertAlmostEqual(rows[-1]['spectral_centroid_hz'], 4000, delta=20)

    def test_click_train_and_bpm(self):
        duration = 18
        x = np.zeros(SAMPLE_RATE * duration, dtype=np.float32)
        # 120 BPM: alternating 45ms wide percussion noise bursts, 0.5sec apart
        rng = np.random.default_rng(17)
        for k in range(1, 2 * duration):
            i = int(k * SAMPLE_RATE / 2)
            length = 800
            x[i:i+length] = (0.7 * rng.standard_normal(length) * np.exp(-np.arange(length)/180)).astype(np.float32)
        rows = run_audio(x)
        self.assertGreater(sum(r['onset'] for r in rows), 20)
        self.assertGreater(rows[-1]['beat_confidence'], 0.25)
        self.assertAlmostEqual(rows[-1]['bpm'], 120, delta=8)
        self.assertGreater(sum(r['beat'] for r in rows), 5)

    def test_tempo_accuracy_multiple_rates(self):
        for bpm in (80, 90, 110, 128, 140, 160, 180):
            duration = 20
            x = np.zeros(SAMPLE_RATE * duration, dtype=np.float32)
            rng = np.random.default_rng(83)
            for k in range(1, int(duration * bpm / 60)):
                start = int(k * SAMPLE_RATE * 60 / bpm)
                n = 600
                x[start:start+n] = (0.65 * rng.standard_normal(n) * np.exp(-np.arange(n)/160)).astype(np.float32)
            result = run_audio(x)[-1]
            self.assertAlmostEqual(result['bpm'], bpm, delta=5, msg=f"tempo={bpm}, result={result['bpm']}")

    def test_random_noise_does_not_generate_drop_without_buildup(self):
        rng = np.random.default_rng(11)
        x = rng.normal(0, .035, SAMPLE_RATE * 16).astype(np.float32)
        rows = run_audio(x)
        self.assertFalse(any(r['drop'] for r in rows))
        self.assertTrue(all(0 <= r['spectral_flatness'] <= 1.001 for r in rows))

    def test_short_audio_no_crash(self):
        x = np.random.default_rng(3).normal(0, 0.02, SAMPLE_RATE).astype(np.float32)
        rows = run_audio(x)
        self.assertEqual(len(rows), 10)
        self.assertTrue(all('drop' in r for r in rows))

if __name__ == '__main__':
    unittest.main()
