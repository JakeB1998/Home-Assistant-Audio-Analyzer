import unittest
from unittest.mock import patch
import numpy as np
from fast_kick import FastKickDetector, FAST_HOP, RATE, MIN_INTERVAL
from playback_stats import PlaybackStats


def detect(audio):
    detector = FastKickDetector(.55)
    out = []
    for start in range(0, len(audio)-FAST_HOP+1, FAST_HOP):
        event = detector.process(audio[start:start+FAST_HOP])
        if event:
            out.append(event)
    return detector, out

class FastKickTests(unittest.TestCase):
    def test_75ms_refractory_and_quiet(self):
        self.assertEqual(MIN_INTERVAL, .075)
        d, events = detect(np.zeros(RATE * 3, dtype=np.float32))
        self.assertFalse(events)
        self.assertEqual(d.accepted, 0)

    def test_detects_repeated_bass_kicks_without_continuous_false_positives(self):
        x = np.zeros(RATE * 6, dtype=np.float32)
        for t in np.arange(.5, 5.95, .5):
            begin = int(t*RATE)
            duration = 2400
            v = np.arange(duration) / RATE
            x[begin:begin+duration] += (.7*np.sin(2*np.pi*72*v)*np.exp(-v*35)).astype('f')
        d, events = detect(x)
        self.assertGreaterEqual(len(events), 9)
        self.assertLessEqual(len(events), 13)
        self.assertGreaterEqual(min(np.diff([e['t_audio'] for e in events])), .075-.002)

    def test_steady_bass_and_bright_noise(self):
        t = np.arange(RATE * 5)/RATE
        _, bass_events = detect((.2*np.sin(2*np.pi*70*t)).astype('f'))
        self.assertEqual(len(bass_events), 0)
        x = np.zeros(RATE*5,dtype=np.float32)
        rng = np.random.default_rng(30)
        for second in np.arange(.5, 4.9, .5):
            a = int(second*RATE)
            z = np.arange(2000)
            x[a:a+2000] += (.5*rng.normal(size=2000)*np.exp(-z/230)).astype('f')
        _, noise_events = detect(x)
        self.assertLessEqual(len(noise_events), 2)

class PlaybackStatsTests(unittest.TestCase):
    def test_rising_edge_buildup_and_report(self):
        stats = PlaybackStats()
        row = { 'energy': .6, 'bass': .5, 'buildup': True, 'onset': True,
                'drop': False,'snare_candidate': True, 'trend': 'rising', 'beat_confidence':.4 }
        stats.observe_metrics(row)
        stats.observe_metrics(row)
        self.assertEqual(stats.events['buildup'], 1)
        stats.observe_kick({'strength':.9,'t_audio':.4})
        stats.observe_kick({'strength':.8,'t_audio':.9})
        report = stats.summary()
        self.assertIn('fast_kicks=2', report)
        self.assertIn('buildups=1', report)
        self.assertIn('avg=', report)
        self.assertIn('KICK INTERVAL', report)

if __name__ == '__main__':
    unittest.main()
