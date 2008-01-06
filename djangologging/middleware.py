import datetime
import inspect
import logging
import os
import re
import sys
import time
import urlparse

import django
from django.conf import settings
from django.contrib import admin
from django.db import connection
from django.shortcuts import render_to_response
from django.template import loader
from django.utils.cache import add_never_cache_headers
try:
    from django.utils.encoding import smart_str
except ImportError:
    # Older versions of Django don't have smart_str, but because they don't
    # require Unicode, we can simply fake it with an identify function.
    smart_str = lambda s: s
from django.utils.functional import curry

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
# to use (or work out) an absolute file path to the templates and add it to 
# TEMPLATE_DIRS.
try:
    template_path = settings.LOGGING_TEMPLATE_DIR
except AttributeError:
    template_path = os.path.join(os.path.dirname(__file__), 'templates')
settings.TEMPLATE_DIRS = (template_path,) + tuple(settings.TEMPLATE_DIRS)

try:
    intercept_redirects = settings.LOGGING_INTERCEPT_REDIRECTS
except AttributeError:
    intercept_redirects = False

try:
    logging_output_enabled = settings.LOGGING_OUTPUT_ENABLED
except AttributeError:
    logging_output_enabled = settings.DEBUG

try:
    logging_show_metrics = settings.LOGGING_SHOW_METRICS
except AttributeError:
    logging_show_metrics = True

try:
    logging_log_sql = settings.LOGGING_LOG_SQL
except AttributeError:
    logging_log_sql = False

if logging_log_sql:
    # Define a new logging level called SQL
    logging.SQL = logging.DEBUG + 1
    logging.addLevelName(logging.SQL, 'SQL')
    
    # Define a custom function for creating log records
    def make_sql_record(frame, original_makeRecord, sqltime, self, *args, **kwargs):
        args = list(args)
        len_args = len(args)
        if len_args > 2:
            args[2] = frame.f_code.co_filename
        else:
            kwargs['fn'] = frame.f_code.co_filename
        if len_args > 3:
            args[3] = frame.f_lineno
        else:
            kwargs['lno'] = frame.f_lineno
        if len_args > 7:
            args[7] = frame.f_code.co_name
        elif 'func' in kwargs:
            kwargs['func'] = frame.f_code.co_name
        rv = original_makeRecord(self, *args, **kwargs)
        rv.__dict__['sqltime'] = '%d' % sqltime
        return rv
    
    class SqlLoggingList(list):
        def append(self, object):
            # Try to find the meaningful frame, rather than just using one from
            # the innards of the Django DB code.
            frame = inspect.currentframe().f_back
            while frame.f_back and frame.f_code.co_filename.startswith(_django_path):
                if frame.f_code.co_filename.startswith(_admin_path):
                    break
                frame = frame.f_back

            sqltime = float(object['time']) * 1000

            # Temporarily use make_sql_record for creating log records
            original_makeRecord = logging.Logger.makeRecord
            logging.Logger.makeRecord = curry(make_sql_record, frame, original_makeRecord, sqltime)
            logging.log(logging.SQL, object['sql'])
            logging.Logger.makeRecord = original_makeRecord
            list.append(self, object)


_redirect_statuses = {
    301: 'Moved Permanently',
    302: 'Found',
    303: 'See Other',
    307: 'Temporary Redirect'}

_django_path = django.__file__.split('__init__')[0]
_admin_path = admin.__file__.split('__init__')[0]


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
        if logging_log_sql:
            connection.queries = SqlLoggingList(connection.queries)
        request.logging_start_time = time.time()

    def process_response(self, request, response):

        if logging_output_enabled and request.META.get('REMOTE_ADDR') in settings.INTERNAL_IPS:

            if intercept_redirects and \
                    response.status_code in _redirect_statuses and \
                    len(handler.get_records()):
                response = self._handle_redirect(request, response)

            if response['Content-Type'].startswith('text/html'):
                self._rewrite_html(request, response)
                add_never_cache_headers(response)

        return response

    def _get_and_clear_records(self):
            records = handler.get_records()
            handler.clear_records()
            for record in records:
                record.formatted_timestamp = format_time(record)
            return records

    def _rewrite_html(self, request, response):
        if not hasattr(request, 'logging_start_time'):
            return
        context = {
            'records': self._get_and_clear_records(),
            'levels': getLevelNames(),
            'elapsed_time': (time.time() - request.logging_start_time) * 1000, # milliseconds
            'query_count': -1,
            'logging_log_sql': logging_log_sql,
            'logging_show_metrics': logging_show_metrics,
            }
        if settings.DEBUG and logging_show_metrics:
            context['query_count'] = len(connection.queries)
            if context['query_count'] and context['elapsed_time']:
                context['query_time'] = sum(map(lambda q: float(q['time']) * 1000, connection.queries))
                context['query_percentage'] = context['query_time'] / context['elapsed_time'] * 100

        header = smart_str(loader.render_to_string('logging.css'))
        footer = smart_str(loader.render_to_string('logging.html', context))

        if close_head_re.search(response.content) and close_body_re.search(response.content):
            response.content = close_head_re.sub(r'%s\1' % header, response.content)
            response.content = close_body_re.sub(r'%s\1' % footer, response.content)
        else:
            # Despite a Content-Type of text/html, the content doesn't seem to
            # be sensible HTML, so just append the log to the end of the
            # response and hope for the best!
            response.write(footer)

    def _handle_redirect(self, request, response):
        if hasattr(request, 'build_absolute_url'):
            location = request.build_absolute_uri(response['Location'])
        else:
            # Construct the URL manually in older versions of Django
            request_protocol = request.is_secure() and 'https' or 'http'
            request_url = '%s://%s%s' % (request_protocol,
                request.META.get('HTTP_HOST'), request.path)
            location = urlparse.urljoin(request_url, response['Location'])
        data = {
            'location': location,
            'status_code': response.status_code,
            'status_name': _redirect_statuses[response.status_code]}
        response = render_to_response('redirect.html', data)
        add_never_cache_headers(response)
        return response