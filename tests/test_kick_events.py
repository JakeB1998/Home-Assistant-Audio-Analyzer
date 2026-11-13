import json
import unittest
from unittest.mock import patch

from analyzer import KICK_EVENT_TOPIC, KickEventPublisher


class FakeClient:
    def __init__(self):
        self.messages = []

    def publish(self, topic, payload, **kwargs):
        self.messages.append((topic, json.loads(payload), kwargs))


class TestKickEventPublisher(unittest.TestCase):
    def test_only_rising_edges_publish(self):
        client = FakeClient()
        publisher = KickEventPublisher()
        frames = [False, True, True, False, True, False]
        with patch('analyzer.time.time', return_value=123.45):
            emitted = [publisher.process({'kick_candidate': candidate, 'kick_strength': 0.78}, client)
                       for candidate in frames]
        self.assertEqual(emitted, [False, True, False, False, True, False])
        self.assertEqual(len(client.messages), 2)
        self.assertEqual([m[1]['count'] for m in client.messages], [1, 2])
        self.assertEqual(client.messages[0],
                         (KICK_EVENT_TOPIC,
                          {'event': 'kick', 'count': 1, 'strength': 0.78, 'timestamp': 123.45},
                          {'qos': 0, 'retain': False}))

    def test_no_client_no_publish(self):
        publisher = KickEventPublisher()
        self.assertFalse(publisher.process({'kick_candidate': True}, None))
        self.assertEqual(publisher.event_count, 0)
