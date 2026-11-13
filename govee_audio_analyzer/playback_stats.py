"""Streaming 60-second playback statistics. No raw audio or track metadata stored."""
from collections import Counter
import logging
import statistics
import time

LOG = logging.getLogger('govee-sendspin')
FIELDS = ('energy','rms','instant_rms','bass','mid','treble','bass_energy',
          'sub_bass_energy','mid_energy','treble_energy','spectral_flux',
          'onset_strength','buildup_score','drop_score','beat_confidence','bpm',
          'dynamic_range_db')
EVENTS = ('onset','beat','kick_candidate','snare_candidate','hihat_candidate',
          'buildup','drop')

class PlaybackStats:
    def __init__(self, interval=60.0):
        self.interval = interval
        self.reset()

    def reset(self):
        self.start = time.monotonic()
        self.frames = 0
        self.events = Counter()
        self.previous = {name:False for name in ('buildup',)}
        self.values = {k:[] for k in FIELDS}
        self.kick_strengths = []
        self.kick_intervals = []
        self.previous_kick_time = None
        self.trends = Counter()
        self.mqtt_publish_attempts = 0
        self.last_metrics = None

    def observe_metrics(self, metrics):
        self.frames += 1
        self.last_metrics = metrics
        for key in FIELDS:
            value = metrics.get(key)
            if isinstance(value, (int,float)):
                self.values[key].append(float(value))
        for key in EVENTS:
            value = bool(metrics.get(key, False))
            if key == 'buildup':
                if value and not self.previous['buildup']:
                    self.events['buildup'] += 1
                self.previous['buildup'] = value
            elif value:
                self.events[key] += 1
        self.trends[str(metrics.get('trend', 'unknown'))] += 1

    def observe_kick(self, event):
        self.events['fast_kick'] += 1
        self.kick_strengths.append(float(event['strength']))
        now = float(event['t_audio'])
        if self.previous_kick_time is not None:
            self.kick_intervals.append(now - self.previous_kick_time)
        self.previous_kick_time = now

    def summary(self, reason='60s active playback'):
        elapsed = max(time.monotonic() - self.start, 0.001)
        lines = ['REACTIVE LIGHTS PLAYBACK REPORT | %s | active_wall=%.1fs | analysis_frames=%d | mqtt_state_attempts=%d' %
                 (reason, elapsed, self.frames, self.mqtt_publish_attempts)]
        lines.append('EVENT COUNTS | fast_kicks=%d | legacy_kicks=%d | snare=%d | hihat=%d | onsets=%d | beats=%d | buildups=%d (rising edges) | drops=%d' %
                     tuple(self.events[x] for x in ('fast_kick','kick_candidate','snare_candidate','hihat_candidate','onset','beat','buildup','drop')))
        lines.append('EVENT RATES/min | fast_kicks=%.1f | snares=%.1f | onsets=%.1f | buildups=%.1f | drops=%.1f' %
                     tuple(60*self.events[x]/elapsed for x in ('fast_kick','snare_candidate','onset','buildup','drop')))
        def fmt(values):
            if not values:
                return 'n/a'
            return 'avg=%.3f median=%.3f min=%.3f max=%.3f stdev=%.3f' % (
                statistics.mean(values), statistics.median(values), min(values), max(values),
                statistics.pstdev(values))
        lines.append('KICK STRENGTH | ' + fmt(self.kick_strengths))
        lines.append('KICK INTERVAL SECONDS | ' + fmt(self.kick_intervals))
        for key in FIELDS:
            lines.append('METRIC %-19s | %s' % (key, fmt(self.values[key])))
        lines.append('ENERGY TREND FRAMES | ' + ', '.join('%s=%s'%(k,v) for k,v in sorted(self.trends.items())))
        lines.append('BPM CONFIDENT FRAMES | %d/%d (beat_confidence >= 0.28)' %
                     (sum(x >= .28 for x in self.values['beat_confidence']), self.frames))
        return '\n'.join(lines)

    def maybe_log(self):
        if time.monotonic() - self.start >= self.interval:
            if self.frames:
                LOG.info('\n%s', self.summary())
            self.reset()

    def finish(self, reason='stream ended'):
        if self.frames:
            LOG.info('\n%s', self.summary(reason))
        self.reset()
