#!/usr/bin/env python

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# makemigrations.py
#
# Call this to make migrations for the django_fsm_ticket models

import os

import django
from django.conf import settings
from django.core.management import call_command

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "django_fsm_ticket"))

settings.configure(
    BASE_DIR=BASE_DIR,
    DEBUG=True,
    SECRET_KEY="something",
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
        }
    },
    INSTALLED_APPS=(
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "rest_framework",
        "django_fsm_ticket",
    ),
    TIME_ZONE="UTC",
    USE_TZ=True,
)

django.setup()
call_command("makemigrations", "django_fsm_ticket")
