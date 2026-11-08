"""Music Assistant Sendspin -> PCM FFT analyzer -> MQTT.

Uses the *public* aiosendspin 9.1.1 client API. Advertises only 22050 Hz,
mono, 16-bit PCM so no codec or resampling step is required. No audio is saved.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import numpy as np

from analyzer import Analyzer, HOP, connect_mqtt, TOPIC, AVAIL

LOG = logging.getLogger("govee-sendspin")
RATE = 22050


class PcmSink:
    """Accumulate PCM chunks and calculate features in 100-ms hops."""

    def __init__(self, mqtt_client=None):
        self.mqtt_client = mqtt_client
        self.analyzer = Analyzer()
        self.buffer = bytearray()
        self.active = False
        self.last_audio = None
        self.frames = 0
        self.last_debug = 0.0

    def start(self, msg):
        self.buffer.clear()
        self.analyzer = Analyzer()
        self.active = True
        LOG.info("Sendspin stream started: %s", msg)

    def clear(self, roles):
        self.buffer.clear()
        self.analyzer = Analyzer()
        LOG.info("Sendspin stream cleared")

    def stop(self, roles):
        self.buffer.clear()
        self.active = False
        LOG.info("Sendspin stream ended")

    def audio(self, timestamp_us, data, audio_format):
        # Callback runs on the event loop: avoid any IO, disk, or blocking code.
        pcm = audio_format.pcm_format
        if str(getattr(audio_format.codec, "value", audio_format.codec)) != "pcm":
            LOG.error("Received non-PCM audio; decoder intentionally not configured")
            return
        if (pcm.sample_rate, pcm.channels, pcm.bit_depth) != (RATE, 1, 16):
            LOG.error("Unexpected audio format %s; expected %s Hz mono s16le", pcm, RATE)
            return
        self.buffer.extend(data)
        # Cap backlog if interrupted; always analyze recent samples.
        if len(self.buffer) > RATE * 2 * 2:
            del self.buffer[:-HOP * 2]
        while len(self.buffer) >= HOP * 2:
            block = bytes(self.buffer[:HOP * 2])
            del self.buffer[:HOP * 2]
            samples = np.frombuffer(block, dtype="<i2").astype(np.float32) / 32768.0
            values = self.analyzer.process(samples)
            self.frames += 1
            self.last_audio = time.monotonic()
            if self.mqtt_client is not None:
                self.mqtt_client.publish(TOPIC, json.dumps(values), qos=0, retain=False)
            else:
                print(json.dumps(values), flush=True)
            if time.monotonic() - self.last_debug > 10:
                self.last_debug = time.monotonic()
                LOG.info("Receiving audio: %s blocks; energy=%.3f, bass=%.3f", self.frames, values["energy"], values["bass"])


def load_identity(path):
    from aiosendspin.noise.keys import Identity
    key_path = Path(path)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        return Identity.from_private_bytes(key_path.read_bytes())
    identity = Identity.generate()
    key_path.write_bytes(identity.private_bytes)
    key_path.chmod(0o600)
    return identity


async def run_once(server, sink, state_dir):
    from aiosendspin.client import SendspinClient
    from aiosendspin.models.player import ClientHelloPlayerSupport, SupportedAudioFormat
    from aiosendspin.models.types import AudioCodec, Roles
    from aiosendspin.noise.trust_store import FileClientPairingStore, PairingPsk
    from aiosendspin.noise.keys import generate_psk, psk_id_for
    from aiosendspin.noise.pairing_token import PSKPairingToken, encode_token

    identity = load_identity(f"{state_dir}/identity.key")
    # v10 pairing stores cannot be parsed by the v9 SDK. Keep the old
    # pairing.json intact; use a separate persistent v9 store instead.
    store = await FileClientPairingStore.open(f"{state_dir}/pairing_v9.json")
    pairing_psk = await store.pairing_psk()
    if pairing_psk is None:
        # Generate exactly once and persist through the SDK's pairing store.
        secret = generate_psk()
        pairing_psk = PairingPsk(psk_id=psk_id_for(secret), psk=secret)
        await store.set_pairing_psk(pairing_psk)
        LOG.info("Created and saved a new per-device Sendspin Pairing PSK")
    pairing_token = encode_token(
        PSKPairingToken(client_id=identity.peer_id, pairing_psk=pairing_psk.psk)
    )
    # The token is a long-lived credential. It is shown intentionally to the
    # operator in the private app logs; do not upload logs to public sites.
    LOG.warning("SENDSPIN PAIRING TOKEN (SECRET): %s", pairing_token)
    LOG.warning("Paste this token into the Music Assistant pairing dialog; keep it private")
    support = ClientHelloPlayerSupport(
        supported_formats=[SupportedAudioFormat(codec=AudioCodec.PCM, channels=1, sample_rate=RATE, bit_depth=16)],
        buffer_capacity=2_000_000,
        supported_commands=[],
    )
    client = SendspinClient(
        identity=identity,
        client_name=os.getenv("SENDSPIN_CLIENT_NAME", "Govee Audio Analyzer"),
        roles=[Roles.PLAYER],
        pairing_store=store,
        player_support=support,
    )
    # Safe here: the client only *receives* player audio, no microphone/source role.
    disconnected = asyncio.Event()
    client.add_disconnect_listener(lambda: disconnected.set())
    client.add_stream_start_listener(sink.start)
    client.add_stream_clear_listener(sink.clear)
    client.add_stream_end_listener(sink.stop)
    client.add_audio_chunk_listener(sink.audio)
    try:
        LOG.info("Connecting to Music Assistant: %s", server)
        await client.connect(server)
        await client.send_player_state(available=True, volume=100, muted=False)
        LOG.info("Sendspin connected. Select/group 'Govee Audio Analyzer' in Music Assistant to deliver audio.")
        await disconnected.wait()
        LOG.warning("Sendspin disconnected")
    finally:
        sink.stop(None)
        await client.disconnect()


async def main():
    server = os.environ.get("SENDSPIN_SERVER", "").strip()
    if not server.startswith(("ws://", "wss://")) or "/sendspin" not in server:
        raise SystemExit("SENDSPIN_SERVER must be ws://HOST:8927/sendspin (or a valid wss:// URL)")
    state_dir = os.getenv("STATE_DIR", "/data")
    os.makedirs(state_dir, exist_ok=True)
    mqtt = None if os.getenv("DRY_RUN", "0") == "1" else connect_mqtt()
    sink = PcmSink(mqtt)
    try:
        while True:
            try:
                await run_once(server, sink, state_dir)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Sendspin connection failed")
            await asyncio.sleep(5)
    finally:
        if mqtt is not None:
            mqtt.publish(AVAIL, "offline", qos=1, retain=True)
            mqtt.loop_stop()
            mqtt.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
