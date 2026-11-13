"""Launch sendspin receiver with Supervisor options."""
import json
import os

with open('/data/options.json', encoding='utf-8') as fp:
    opts = json.load(fp)
for key, value in {
    'SENDSPIN_SERVER': opts['sendspin_server'],
    'MQTT_HOST': opts['mqtt_host'],
    'MQTT_PORT': opts['mqtt_port'],
    'MQTT_USER': opts['mqtt_user'],
    'MQTT_PASSWORD': opts['mqtt_password'],
    'STATE_DIR': '/data',
    'LOG_LEVEL': opts.get('log_level', 'DEBUG'),
    'KICK_SENSITIVITY': opts.get('kick_sensitivity', 0.55),
}.items():
    os.environ[key] = str(value)
os.execv('/opt/venv/bin/python', ['/opt/venv/bin/python', '-u', '/opt/analyzer/sendspin_receiver.py'])
