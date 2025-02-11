import re
from django_hosts import patterns, host

host_patterns = patterns(
    "",
    host(re.sub(r"_", r"-", r"mariner_app"), "mariner_app.urls", name="mariner_app"),
)
