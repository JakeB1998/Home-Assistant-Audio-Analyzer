FROM ghcr.io/home-assistant/base:3.24
RUN apk add --no-cache python3 py3-numpy py3-pip py3-cryptography ffmpeg build-base python3-dev linux-headers && \
    python3 -m venv --system-site-packages /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir 'aiosendspin==10.2.1' 'paho-mqtt>=2,<3'
COPY analyzer.py sendspin_receiver.py run.py /opt/analyzer/
CMD ["/opt/venv/bin/python", "-u", "/opt/analyzer/run.py"]
