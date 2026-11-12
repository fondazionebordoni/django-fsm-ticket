# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.0] - YYYY-MM-DD

### Security

This release fixes several security issues. All users are encouraged to upgrade.
See the linked advisories for details, affected configurations and workarounds.

- REST API: internal ticket fields (`state`, `ts`, `last_update`, `user`) are now read-only
  ([GHSA-v59j-g75x-9xmm](https://github.com/fondazionebordoni/django-fsm-ticket/security/advisories/GHSA-v59j-g75x-9xmm), CVE-xxxx-xxxxx).
- REST API: the action endpoint now enforces `Ticket.is_visible()`
  ([GHSA-v4xh-28jr-f5v5](https://github.com/fondazionebordoni/django-fsm-ticket/security/advisories/GHSA-v4xh-28jr-f5v5), CVE-xxxx-xxxxx).
- Notifications and reminders now honour `TicketUpdate.is_visible()`
  ([GHSA-GHSA-58xg-c7pj-9qqp](https://github.com/fondazionebordoni/django-fsm-ticket/security/advisories/GHSA-58xg-c7pj-9qqp), CVE-xxxx-xxxxx).
- REST API: counts and pages of list endpoints no longer depend on non-visible items
  ([GHSA-m935-5m8q-7wgm](https://github.com/fondazionebordoni/django-fsm-ticket/security/advisories/GHSA-m935-5m8q-7wgm), CVE-xxxx-xxxxx).
- Attachment deletion is now atomic, and enforces the state and permission of the `delete_attachment` transition
  ([GHSA-w8vm-x6rg-g58x](https://github.com/fondazionebordoni/django-fsm-ticket/security/advisories/GHSA-w8vm-x6rg-g58x), CVE-xxxx-xxxxx).
- REST API: the browsable API is only served when `DEBUG = True`: its HTML forms listed related objects
  (e.g. notes of all ticket updates, all users) regardless of visibility
  ([GHSA-xxxx-xxxx-xxxx](https://github.com/fondazionebordoni/django-fsm-ticket/security/advisories/GHSA-xxxx-xxxx-xxxx), CVE-xxxx-xxxxx).

### Security hardening

- REST API: `TicketUpdate.limit_view_querysets()` is now applied to related fields, as in HTML action forms.
  `DeleteAttachmentTicketUpdate` now limits attachments to those of the same ticket.
- Actions now lock the ticket in the database and check the transition against its current state,
  so that concurrent actions on the same ticket can no longer both succeed or fork the update history.
- Notifications are sent only after the transaction is committed, and errors in sending a notification
  no longer abort the action or prevent other notifications. Recipient addresses are now properly quoted.
- Inactive users are no longer notified, nor considered as users in charge of tickets.
- Attachment downloads are streamed from storage, without temporary copies.
- REST API: ordering on fields listed in `exclude_fields` is no longer allowed.
- REST API: the file of deleted attachments is only shown to superusers, as in HTML views.
- Non-visible tickets and ticket updates now consistently look like non-existing ones (404 in the REST API).
- Invalid attachment UUIDs in URLs now return 404 instead of 500.
- REST API: `ActionError` raised by actions now returns 400 instead of 500, as in HTML views.

### Added

- `FSM_TICKET_API_PREFIX` can be set to `None` to disable the REST API. Invalid values (non-strings, empty prefixes)
  now raise `ImproperlyConfigured`.
- `API_MAX_LIMIT` option of `FSM_TICKET_CONFIG`: bounds the page size of list endpoints.
- `AttachmentTicketUpdate.max_file_size`: maximum size of attached files, configurable per action
  by using a subclass (even a proxy model) as `ticket_update_model`.
- `LimitQuerysetsMixin`, to apply `limit_view_querysets()` in custom `write_serializer`s.

### Fixed

- `user_to_mail_recipient()` always returns a plain `str`, even when the email address is a lazy object
  (e.g. read from django-constance): notifications can be serialized, e.g. to be sent through Celery.

### Deprecated

- Leaving `FSM_TICKET_API_PREFIX` unset emits a `FutureWarning`: currently the REST API is enabled
  by default (with prefix `"api"`) whenever its dependencies are installed, but starting from 2.0 it will
  be disabled unless explicitly configured. Set `FSM_TICKET_API_PREFIX = None` to disable the API,
  or to a prefix (e.g. `"api"`) to keep it enabled.
- `API_MAX_LIMIT` currently defaults to no limit: list endpoints are paginated only if `limit` is requested,
  and the page size is not bounded. Starting from 2.0 it will default to 25: list endpoints will always
  be paginated, even if no `limit` is requested, and larger limits will be reduced to 25. Clients that do
  not pass `limit` will then receive a paginated response (`count`, `next`, `previous`, `results`)
  instead of a plain list. Leaving `API_MAX_LIMIT` unset now emits a `FutureWarning`: set it to the
  maximum page size, or to `None` to keep pages unbounded.

### Upgrade notes

- **Browsable API:** unless `DEBUG = True`, generated API views no longer serve the browsable API
  (`?format=api` returns 404, requests accepting only HTML return 406). Custom API views are not affected:
  use `django_fsm_ticket.api.api_renderers()` to get the same behavior.
- **Notifications** are sent after the current transaction is committed. In Django `TestCase`s,
  where transactions are never committed, use `self.captureOnCommitCallbacks(execute=True)`
  before checking `mail.outbox`. Exceptions raised by a custom `send_notification()` are now logged
  instead of propagated. Users without an email address are skipped by the default `send_notification()`.
- **Visibility and performance:** if `is_visible()` is overridden, list endpoints of the REST API check
  the visibility of all the prefiltered items before paginating. Express visibility rules in
  `prefilter_visible()` as much as possible, and override `is_visible()` only for rules that cannot be
  expressed as a queryset.
- **REST API responses:** non-visible tickets and ticket updates return 404 instead of 403;
  an action made impossible by a concurrent action returns 409; `ActionError` returns 400.
- **Custom `write_serializer`s** of ticket updates get the limits of `limit_view_querysets()` only if they
  derive from `TicketUpdateBaseCreateSerializer` or include `LimitQuerysetsMixin`, and then require
  the `ticket` in the serializer context (provided by the action endpoint).
- **Validation in custom `form`s and `Model.clean()`** is not applied by the REST API: if the API is
  enabled, replicate it in `write_serializer`.
- **Attachment deletion** now requires the `delete_attachment` transition to be allowed from the current
  state, and its `permission` (if any) to be granted, in addition to `can_delete_attachment()`.
- **Actions** run in a transaction and lock the ticket: `@action` must be applied above `@transition`
  (as in the documentation). An action made impossible by a concurrent one raises
  `django_fsm.TransitionNotAllowed`.

## [1.4.0.post1] - 2026-05-20

- Packaging fixes; fully qualified URLs in the README, so that they work on PyPI.

## [1.4.0] - 2026-05-20

- First public release.

[1.5.0]: https://github.com/fondazionebordoni/django-fsm-ticket/compare/v1.4.0.post1...v1.5.0
[1.4.0.post1]: https://github.com/fondazionebordoni/django-fsm-ticket/releases/tag/v1.4.0.post1
[1.4.0]: https://pypi.org/project/django-fsm-ticket/1.4.0/
