import logging
import os
import re

from django.conf import settings
from django.template import loader

from djangologging import getLevelNames
from djangologging.handlers import ThreadBufferedHandler


""" Regex to find the closing head element in a (X)HTML document. """
close_head_re = re.compile("(</head>)", re.M | re.I)

""" Regex to find the closing body element in a (X)HTML document. """
close_body_re = re.compile("(</body>)", re.M | re.I)


# Initialise and register the handler
handler = ThreadBufferedHandler()
handler.setLevel(logging.NOTSET)
handler.setFormatter(logging.Formatter())
logging.root.setLevel(logging.NOTSET)
logging.root.addHandler(handler)


class LoggingMiddleware(object):
    """
    Middleware that uses the appends messages logged during the request to the
    response (if the response is HTML).
    """

    def process_request(self, request):
        handler.clear_records()

    def process_response(self, request, response):
        if settings.DEBUG and request.META.get('REMOTE_ADDR') in settings.INTERNAL_IPS:
            if response['Content-Type'].startswith('text/html'):
                self._rewrite_html(response)
        return response

    def _get_and_clear_records(self):
            records = handler.get_records()
            handler.clear_records()
            def formatted_time(record):
                time = handler.formatter.formatTime(record, '%H:%M:%S')
                return '%s,%03d' % (time, record.msecs)
            for record in records:
                record.formatted_timestamp = formatted_time(record)
            return records

    def _rewrite_html(self, response):
        records = self._get_and_clear_records()

        # Because this logging module isn't registered within INSTALLED_APPS,
        # we have to work out an absolute file path to the templates.
        template_path = os.path.join(os.path.dirname(__file__), 'templates')

        css_template = os.path.join(template_path, 'logging.css')
        header = loader.render_to_string(css_template)

        html_template = os.path.join(template_path, 'logging.html')
        levels = getLevelNames()
        footer = loader.render_to_string(html_template, {'records': records, 'levels': levels})

        if close_head_re.search(response.content) and close_body_re.search(response.content):
            response.content = close_head_re.sub(r'%s\1' % header, response.content)
            response.content = close_body_re.sub(r'%s\1' % footer, response.content)
        else:
            # Despite a Content-Type of text/html, the content doesn't seem to
            # be sensible HTML, so just append the log to the end of the
            # response and hope for the best!
            response.write(footer)