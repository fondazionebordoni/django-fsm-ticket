# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import os
import shutil
import mimetypes
from contextlib import contextmanager
from inspect import isfunction
from functools import wraps
import tempfile

from django.http import FileResponse
from django.utils.module_loading import import_string
from django.utils.text import get_valid_filename


def import_path_or_string(path_or_string):
    """
    returns method/class, importing it if `path_or_string` is a string

    :param path_or_string: path to class or method
    :return: class or method
    """
    if path_or_string and isinstance(path_or_string, str):
        return import_string(path_or_string)
    return path_or_string


@contextmanager
def temp_dir(keep=False):
    """
    Context manager. Create and yield a temporary dir, and destroys it on exit
    """
    t = tempfile.mkdtemp()
    try:
        yield t
    finally:
        if not keep:
            shutil.rmtree(t)


class ThresholdMap:
    def __init__(self, range_map):
        """
        Map values belonging to ranges

        range_map is a tuple like this:
        (value_0, threshold_1, value_1, ..., threshold_n, value_n)

        For example:
        tm = ThresholdMap('low', 5, 'medium', 10, 'high')
        tm[1] --> 'low'
        tm[5] --> 'medium'
        tm[12] --> high
        """
        # TODO: improve cost using a data structure, such as an AVL Tree
        self.range_map = range_map

    def map_and_get_level(self, item):
        """
        Return a pair (mapped_value, mapped_value_index)

        mapped_value_index starts from 0, and increases by 1 at each threshold
        """
        v = 0
        i = 1
        n = len(self.range_map)
        while i < n and self.range_map[i] <= item:
            v += 2
            i += 2
        return self.range_map[v], v / 2

    def __getitem__(self, item):
        return self.map_and_get_level(item)[0]

    def get_values(self):
        """
        Return sorted list of values
        """
        out = []
        i = 0
        n = len(self.range_map)
        while i < n:
            out.append(self.range_map[i])
            i += 2
        return out


def decorator_factory(df):
    """
    Decorator that adds a special "no params" mode to a decorator factory

    If df is a decorator factory, by decorating df with this decorator
    you get a function that acts either as a decorator factory (ordinarily),
    or directly as a decorator, if it is fed with only one parameter which is
    a function. Thus the following become equivalent:
    ```
    @df()
    def f(*args, **kwargs):
        ...

    @df
    def f(*args, **kwargs):
        ...
    ```
    """

    @wraps(df)
    def g(*args, **kwargs):
        if len(kwargs) == 0 and len(args) == 1:
            f = args[0]
            if isfunction(f):
                return df()(f)
        return df(*args, **kwargs)

    return g


def guess_mimetype_with_default(url, default="application/octet-stream"):
    """
    Guess and return the mimetype, given the file name, path or url.

    Return the mimetype string, or the default if the mimetype cannot be determined.
    """
    mimetype, dummy = mimetypes.guess_type(url)
    if mimetype is None:
        return default
    return mimetype


def copy_django_modelfile_to_dir(modelfile, filename, chunk_size=1024 * 1024):
    """
    Copy a Django FieldFile or UploadedFile to a local file inside a temporary dir

    Use streaming to avoid loading the whole file into memory

    Works with:
    - FileSystemStorage
    - S3 / MinIO (django-storages)
    - UploadedFile (InMemory and Temporary)
    """
    opened_here = False

    if hasattr(modelfile, "open"):
        modelfile.open("rb")
        opened_here = True

    try:
        with open(filename, "wb") as destination:
            if hasattr(modelfile, "chunks"):
                for chunk in modelfile.chunks(chunk_size):
                    destination.write(chunk)
            else:
                while True:
                    data = modelfile.read(chunk_size)
                    if not data:
                        break
                    destination.write(data)
    finally:
        if opened_here:
            try:
                modelfile.close()
            except Exception:
                pass


def _safe_temp_filename(filename, fallback="file"):
    base = os.path.basename(filename)
    safe = get_valid_filename(base).strip()
    return safe if safe else fallback


@contextmanager
def temporary_filemodel(modelfile, keep=False, filename=None):
    """
    Create and yield a temporary local copy of a modelfile, or an uploaded file

    If `filename` is not None, save the file using `filename` instead
    of model file name in field
    """
    with temp_dir(keep) as path:
        if filename is None:
            filename = modelfile.name
        filename = _safe_temp_filename(filename)
        filepath = os.path.join(path, filename)
        copy_django_modelfile_to_dir(modelfile, filepath)
        yield filepath


def serve_file(file_path, served_name=None):
    """
    Load a file and serve its content

    `served_name` is the name of the file which will be served. If not specified,
    it will be the basename of `file_path`
    """
    if served_name is None:
        served_name = os.path.basename(file_path)
    return FileResponse(
        open(file_path, "rb"),
        content_type=guess_mimetype_with_default(file_path),
        as_attachment=True,
        filename=served_name,
    )


def user_verbose(user):
    """
    Returns a string for the given Django user: first and last name, if defined, otherwise username
    """
    name = f"{user.first_name} {user.last_name}"
    if name.strip() == '':
        return user.username
    return name
