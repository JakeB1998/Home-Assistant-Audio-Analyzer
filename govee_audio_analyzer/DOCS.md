# Govee Sendspin Audio Analyzer v0.3

Receives real-time PCM from Music Assistant using `aiosendspin` 10.2.1 (Python), analyzes 100-ms windows, and publishes MQTT discovery sensors to Home Assistant. No LedFx, C++ client, or second audio analyzer needed.

**Status:** Python code and synthetic audio tests are provided. The encrypted live Sendspin handshake, Home Assistant app build, and MQTT broker interaction have **not** been end-to-end tested against your server. Verify your Music Assistant version speaks the current Sendspin v1 protocol. Music Assistant may require enabling unpaired playback for this player.

## Home Assistant OS installation

1. Ensure Music Assistant and the Mosquitto broker are installed. Make a dedicated MQTT username/password in Home Assistant.
2. Copy the `home_assistant_app/govee_audio_analyzer/` directory to `/addons/local/govee_audio_analyzer/` on your HA OS machine (for example using Studio Code Server or Samba). The package includes all three Python files in the add-on folder.
3. Go to **Settings → Apps → App Store → ⋮ → Check for updates** (or reload the store). Under **Local apps**, install **Govee Sendspin Audio Analyzer**.
4. Set `sendspin_server` to `ws://YOUR_MUSIC_ASSISTANT_HOST:8927/sendspin`. Do not use Music Assistant port 8095 for a third-party client. Fill MQTT host/port/user/password. Use the local IP if your `.local` domain fails DNS resolution inside the app.
5. Start the app; inspect its **Logs** for `Sendspin connected`.
6. Music Assistant → Players: find **Govee Audio Analyzer**. Enable it and group it with Apollo Cast. The analyzer does not play sound.
7. While playing, app Logs should show `Receiving audio:` every ~10 seconds. Developer Tools → States should show the `sensor.audio_energy`, `sensor.audio_bass`, `sensor.audio_energy_trend` and other discovered entities (Home Assistant may append a suffix if names clash). Use the *actual* entity IDs shown in HA for automations.

## Docker Compose alternative

Copy `.env.example` to `.env`; update hostname and MQTT credentials; run `docker compose up --build -d` on a Docker-capable machine. This is not executed in the Home Assistant UI.

## Audio behavior and limitations

- Advertises **PCM 22050 Hz / 16-bit / mono**, asks Music Assistant to resample, and processes the stream without a separate decoder or FFmpeg.
- Computes adaptive loudness, spectral band fractions, onsets, and heuristic buildup/drop indicators. `bass` is relative bass *share*, not calibrated absolute bass power. `trend` and drops are heuristic.
- Publishes approximately 10 readings/second. The receiver never saves audio to disk. Device identity and pairing state are saved under `/data`.
- Unpaired listening is enabled for this *output-only* client. Restrict to your trusted LAN; update pairing policy later if needed.
- MQTT Discovery availability goes offline on graceful shutdown; `expire_after` marks stale numeric readings unavailable during interruption.
- Current project does not implement loudspeaker synchronization delays. It receives samples as delivered by Sendspin. Light mode changes through Home Assistant are slower than beat-by-beat lighting updates.

## Source references

- https://github.com/Sendspin/aiosendspin
- https://www.music-assistant.io/player-support/sendspin/
