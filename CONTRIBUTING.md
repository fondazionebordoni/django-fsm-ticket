# Contributing to django-fsm-ticket

Thank you for your interest in contributing to **django-fsm-ticket**!  
This project is developed and maintained by the **Fondazione Ugo Bordoni** with the goal of providing open-source tools for ticket and workflow management based on finite state machines (FSM) in Django.

We welcome contributions of all kinds — code, documentation, testing, bug reports, and suggestions.

---

## 🧩 Getting Started

### 1. Fork and clone the repository

Fork the repository on GitHub and clone your copy locally:

```
git clone https://github.com/fondazionebordoni/django-fsm-ticket.git
cd django-fsm-ticket
```

### 2. Set up the development environment

This project uses [Poetry](https://python-poetry.org/) for dependency management and packaging.

If Poetry is not installed, you can install it with:

```
pip install --user poetry
```

Then install all dependencies, including development tools:

```
poetry install --all-extras
```

This will create a virtual environment and install both runtime and development dependencies. It will also allow you to run the example project.

To activate the virtual environment:

```bash
eval "$(poetry env activate)"
```

or, if you use `fish`:

```fish
eval (poetry env activate)
```

### 3. Run the test suite

To run tests:

```
poetry run python runtests.py
```

(or simply ```python runtests.py``` if you activated the virtual environment).


### 4. Make migrations

If you make changes to the models, you can make migrations without a functioning Django app:

```
poetry run python makemigrations.py
```

### 5. Running the example project

We provide a Django project that uses `django-fsm-ticket` for your convenience:

```
eval "$(poetry env activate)"
cd example_project

# Setup database
python manage.py migrate
python manage.py createsuperuser

# Start server
python manage.py runserver
```

Note that the `example_project` uses the `django_fsm_ticket` app in _editable_ mode.
This means you can modify files directly in the `django_fsm_ticket` folder to change and test the library's behavior, without having to publish or reinstall it from PyPI.

---

## 🧹 Code Style

Follow [PEP 8](https://peps.python.org/pep-0008/) conventions and use **Ruff** (or `flake8`) for linting.

Before submitting a pull request, make sure that:

```
poetry run ruff check .
poetry run python runtests.py
```

run successfully without errors.

You can also enable automatic checks before commits:

```
pre-commit install
```

---

## 🧪 Adding Tests

Every new feature or bug fix should include at least one automated test.  
Tests live in the `tests/` directory.

---

## 🔀 Pull Requests

1. Create a descriptive branch name:
   ```
   git checkout -b feature/short-description
   ```
2. Make your changes.
3. Run the tests to ensure everything works.
4. Commit using clear, concise messages.
5. Open a Pull Request (PR) to the branch `main`.
6. Fill in the PR description explaining what has been added or changed.

All PRs will be reviewed by the FUB's maintainers or designated reviewers.

---

## 🐞 Reporting Bugs and Requesting Features

If you find a bug or have an idea for a new feature, please open an issue on GitHub and include:

- A clear and descriptive title;
- Steps to reproduce the problem (if applicable);
- Expected vs. actual behavior;
- Your environment details (Python, Django, and library version).

---

## 🧾 Versioning

This project follows [Semantic Versioning](https://semver.org/):  
`MAJOR.MINOR.PATCH` (e.g., `1.4.0`).

---


## 🚀 Release & Publish (Automated)

This project uses **GitHub Actions** to build and publish releases to **PyPI** automatically.

Publishing a new version is a **tag-driven process**: once a version tag is pushed to the repository, the CI workflow will take care of building the package, uploading it to PyPI, and creating a GitHub Release.

---

Step-by-step procedure

### 1. Update the project version

Update the version in `pyproject.toml` using Poetry:

```bash
poetry version X.Y.Z
```

This ensures that the version declared in the source code matches the release version.

---

### 2. Commit the version bump

```bash
git commit -am "Release X.Y.Z"
```

---

### 3. Create a Git tag

Create a version tag following the `vX.Y.Z` convention:

```bash
git tag vX.Y.Z
```

---

### 4. Push commits and tags

```bash
git push origin main --tags
```

---

### What happens automatically

Pushing the tag triggers the GitHub Actions workflow, which will:

- build the source distribution and wheel using Poetry
- publish the package to **PyPI**
- create a **GitHub Release** associated with the tag
  - release notes are generated automatically

No manual build or publish commands are required.

---

PyPI does not allow overwriting an existing release.  
If a release fails, fix the issue, bump the version, and create a new tag.

---


That’s it: once the tag is pushed, your package will be published automatically 🎉

## 🤝 Conduct and Communication

All communication in this project should remain professional and respectful.

For any concerns, please reach out to [opensource-group+django-fsm-ticket@fub.it](mailto:opensource-group+django-fsm-ticket@fub.it).

---

## 💌 Contact

For questions, institutional collaboration, or support:

**Fondazione Ugo Bordoni**  
📧 [opensource-group+django-fsm-ticket@fub.it](mailto:opensource-group+django-fsm-ticket@fub.it)  
🌐 [https://www.fub.it](https://www.fub.it)

---

Thank you for helping make `django-fsm-ticket` better 💙
