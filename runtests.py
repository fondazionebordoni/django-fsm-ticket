#!/usr/bin/env python3

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Approach from https://docs.djangoproject.com/en/5.2/topics/testing/advanced/
from argparse import ArgumentParser
import os
import sys

import django
from django.conf import settings
from django.test.utils import get_runner

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_arguments():
    parser = ArgumentParser()
    parser.add_argument(
        "-t",
        "--tag",
        default="",  # this somehow returns all row
        help="Tags separated by ','",
    )


def run():
    parser = ArgumentParser(description="Run tests")
    parser.add_argument(
        "-t",
        "--tag",
        help="Tags separated by ','",
    )
    args = parser.parse_args()

    settings.configure(
        SECRET_KEY="fake-key",
        INSTALLED_APPS=[
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "rest_framework",
            "django_filters",
            "django_fsm_ticket",
            "tests",
        ],
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
            }
        },
        ROOT_URLCONF="django_fsm_ticket.urls",
        MIDDLEWARE=[
            "django.middleware.security.SecurityMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
        ],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [
                        "django.template.context_processors.debug",
                        "django.template.context_processors.request",
                        "django.contrib.auth.context_processors.auth",
                    ],
                },
            },
        ],
        TEMPLATE_DIRS=[
            os.path.join("django_fsm_ticket/templates/"),
        ],
    )
    django.setup()
    django.core.management.call_command("makemigrations", "tests")
    TestRunner = get_runner(settings)
    test_runner = TestRunner(tags=args.tag.split(",") if args.tag else None)
    failures = test_runner.run_tests(["tests"])
    sys.exit(bool(failures))


if __name__ == "__main__":
    run()
