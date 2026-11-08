# Govee Audio Analyzer — Home Assistant app repository

This repository contains a community Home Assistant app that receives Sendspin audio from Music Assistant, calculates audio measurements, and publishes MQTT discovery sensors.

## Install from Home Assistant

1. Create a **public GitHub repository** and upload the files in this directory **to the root** of the repository. The root must contain `repository.yaml` and `govee_audio_analyzer/`.
2. In Home Assistant go to **Settings → Apps → Install app → ⋮ → Repositories**.
3. Add the complete GitHub repository URL.
4. Open **Govee Sendspin Audio Analyzer**, install it, configure the Sendspin server URL and MQTT credentials, and start it.
5. Enable the analyzer's Sendspin player in Music Assistant and play to it (or group it with your speaker if compatible).
6. Review app logs and MQTT sensors in Developer Tools → States.

**Status:** The Python analyzer and simulated PCM callback tests have passed; the encrypted Sendspin connection and Home Assistant app build have *not* been tested on your server. Installation may require debugging.

**Security:** Never commit MQTT passwords, Sendspin pairing state, or other secrets to GitHub. Enter credentials in the Home Assistant app's Configuration tab after installation. The repository's `config.yaml` has blank credentials.

**Deployment:** This uses a local build on the Home Assistant host (Dockerfile), rather than a prebuilt container image. Building can take some time and requires network access and sufficient free space. For a reliable production release, publish a multi-architecture image using GitHub Actions.


## 0.3.2 compatibility fix

Pinned `aiosendspin==9.1.1` to match the older Noise handshake used by Music Assistant 2.10.x and adjusted the three-argument audio callback. This specifically targets the `psk_category` missing-field error from the previous 0.3.1 build. It is not yet validated against the user's live Music Assistant server. If the server uses a newer protocol version, verify the Music Assistant version before choosing a different SDK.
