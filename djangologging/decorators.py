from djangologging import SUPPRESS_OUTPUT_ATTR


def supress_logging_output(func=None):
    def decorated(*args, **kwargs):
        response = func(*args, **kwargs)
        setattr(response, SUPPRESS_OUTPUT_ATTR, True)
        return response
    return decorated