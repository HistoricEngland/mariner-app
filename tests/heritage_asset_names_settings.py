"""Test database and services from the running application environment."""

from copy import deepcopy

from mariner_app import settings as project_settings
from .test_settings import *


DATABASES = deepcopy(project_settings.DATABASES)
ELASTICSEARCH_HOSTS = project_settings.ELASTICSEARCH_HOSTS
