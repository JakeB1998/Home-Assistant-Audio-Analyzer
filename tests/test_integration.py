"""Integration checks without a live Sendspin or MQTT server."""
import json
import sys
import types
import unittest
from unittest.mock import patch

import numpy as np

from analyzer import HOP, SAMPLE_RATE, SENSORS, BINARY, connect_mqtt, TOPIC, AVAIL
from sendspin_receiver import PcmSink


class FakeMQTTClient:
    def __init__(self, *args, **kwargs):
        self.published = []

    def username_pw_set(self, *args):
        pass

    def will_set(self, *args, **kwargs):
        pass

    def connect(self, *args):
        pass

    def loop_start(self):
        pass

    def publish(self, topic, payload, **kwargs):
        self.published.append((topic, payload, kwargs))


class ReceiverTests(unittest.TestCase):
    def test_callback_emits_complete_json_and_flushes_on_restart(self):
        mqtt = FakeMQTTClient()
        sink = PcmSink(mqtt)
        sink.start({"source": "synthetic"})
        audio_format = types.SimpleNamespace(
            codec=types.SimpleNamespace(value="pcm"),
            pcm_format=types.SimpleNamespace(sample_rate=SAMPLE_RATE, channels=1, bit_depth=16),
        )
        wave = (np.sin(np.arange(HOP * 3) * 2 * np.pi * 85 / SAMPLE_RATE) * 5000).astype('<i2')
        sink.audio(0, wave[:1000].tobytes(), audio_format)
        self.assertEqual(len(mqtt.published), 0)
        sink.audio(0, wave[1000:].tobytes(), audio_format)
        self.assertEqual(len(mqtt.published), 3)
        self.assertTrue(all(topic == TOPIC for topic, _, _ in mqtt.published))
        payload = json.loads(mqtt.published[-1][1])
        self.assertEqual(set(payload), set(SENSORS) | set(BINARY))
        self.assertGreater(payload['bass'], 0.9)
        sink.stop(None)
        sink.start({})
        self.assertEqual(len(sink.buffer), 0)
        self.assertEqual(sink.analyzer.sample_count, 0)

    def test_invalid_audio_format_is_discarded(self):
        mqtt = FakeMQTTClient()
        sink = PcmSink(mqtt)
        sink.start({})
        fmt = types.SimpleNamespace(
            codec=types.SimpleNamespace(value="pcm"),
            pcm_format=types.SimpleNamespace(sample_rate=48000, channels=2, bit_depth=16))
        sink.audio(0, b'\x00' * (HOP * 2), fmt)
        self.assertFalse(mqtt.published)

    def test_discovery_preserves_legacy_topics_and_expires_binary_states(self):
        fake_mqtt = types.ModuleType('paho.mqtt.client')
        fake_mqtt.Client = FakeMQTTClient
        fake_mqtt.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)
        fake_parent = types.ModuleType('paho')
        fake_sub = types.ModuleType('paho.mqtt')
        fake_parent.mqtt = fake_sub
        fake_sub.client = fake_mqtt
        with patch.dict(sys.modules, {'paho': fake_parent, 'paho.mqtt': fake_sub, 'paho.mqtt.client': fake_mqtt}):
            client = connect_mqtt()
        configs = {topic: json.loads(payload) for topic, payload, _ in client.published if topic.endswith('/config')}
        self.assertEqual(len(configs), len(SENSORS) + len(BINARY))
        self.assertEqual(configs['homeassistant/sensor/govee_audio_energy/config']['unique_id'], 'govee_audio_energy')
        self.assertEqual(configs['homeassistant/binary_sensor/govee_audio_drop/config']['expire_after'], 5)
        self.assertEqual(configs['homeassistant/sensor/govee_audio_bpm/config']['state_topic'], TOPIC)
        self.assertIn((AVAIL, 'online', {'retain': True}), client.published)

if __name__ == '__main__':
    unittest.main()
