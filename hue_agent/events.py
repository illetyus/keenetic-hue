import json
import logging


def emit(event, **fields):
    logging.getLogger('hue-agent').warning(json.dumps(dict(event=event, **fields), allow_nan=False))
