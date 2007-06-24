import datetime
import logging
import os
import re
import urlparse

from django.conf import settings
from django.shortcuts import render_to_response
from django.template import loader
from django.utils.cache import add_never_cache_headers

from djangologging import getLevelNames
from djangologging.handlers import ThreadBufferedHandler


""" Regex to find the closing head element in a (X)HTML document. """
close_head_re = re.compile("(</head>)", re.M | re.I)

""" Regex to find the closing body element in a (X)HTML document. """
close_body_re = re.compile("(</body>)", re.M | re.I)


# Initialise and register the handler
handler = ThreadBufferedHandler()
logging.root.setLevel(logging.NOTSET)
logging.root.addHandler(handler)

# Because this logging module isn't registered within INSTALLED_APPS, we have
# to work out an absolute file path to the templates.
template_path = os.path.join(os.path.dirname(__file__), 'templates')

try:
    intercept_redirects = settings.LOGGING_INTERCEPT_REDIRECTS
except AttributeError:
    intercept_redirects = False

try:
    logging_output_enabled = settings.LOGGING_OUTPUT_ENABLED
except AttributeError:
    logging_output_enabled = settings.DEBUG

_redirect_statuses = {
    301: 'Moved Permanently',
    302: 'Found',
    303: 'See Other',
    307: 'Temporary Redirect'}


def format_time(record):
    time = datetime.datetime.fromtimestamp(record.created)
    return '%s,%03d' % (time.strftime('%H:%M:%S'), record.msecs)

class LoggingMiddleware(object):
    """
    Middleware that uses the appends messages logged during the request to the
    response (if the response is HTML).
    """

    def process_request(self, request):
        handler.clear_records()

    def process_response(self, request, response):

        if logging_output_enabled and request.META.get('REMOTE_ADDR') in settings.INTERNAL_IPS:

            if intercept_redirects and \
                    response.status_code in _redirect_statuses and \
                    len(handler.get_records()):
                response = self._handle_redirect(request, response)

            if response['Content-Type'].startswith('text/html'):
                self._rewrite_html(response)

        return response

    def _get_and_clear_records(self):
            records = handler.get_records()
            handler.clear_records()
            for record in records:
                record.formatted_timestamp = format_time(record)
            return records

    def _rewrite_html(self, response):
        records = self._get_and_clear_records()

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

    def _handle_redirect(self, request, response):
        request_protocol = request.is_secure() and 'https' or 'http'
        request_url = '%s://%s' % (request_protocol, request.META.get('HTTP_HOST'))
        location = urlparse.urljoin(request_url, response['Location'])
        redirect_template = os.path.join(template_path, 'redirect.html')
        data = {
            'location': location,
            'status_code': response.status_code,
            'status_name': _redirect_statuses[response.status_code]}
        response = render_to_response(redirect_template, data)
        add_never_cache_headers(response)
        return response