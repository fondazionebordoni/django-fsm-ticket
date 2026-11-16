# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from rest_framework import serializers

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import reverse
from django.utils.module_loading import import_string

from django_fsm_ticket.models import AttachmentTicketUpdate, Ticket
from django_fsm_ticket.utils import user_verbose


class GetUserMixin:
    def _get_current_user(self):
        request = self.context.get("request", None)
        if request:
            return request.user
        return AnonymousUser()


class TicketActionSerializer(serializers.Serializer):
    name = serializers.CharField()
    verbose_name = serializers.CharField()
    priority = serializers.FloatField()


class BaseTicketSerializer(serializers.ModelSerializer):
    """Serializer for custom response"""

    class Meta:
        model = Ticket


def ticket_list_serializer_factory(ticket_engine):
    """
    Returns a serializer suitable for listing ticket, excluding
    fields that may slow down the respone
    """

    class TicketSerializer(serializers.ModelSerializer):
        state_verbose = serializers.CharField(read_only=True)

        class Meta:
            model = ticket_engine.TicketClass
            exclude = ["polymorphic_ctype"] + ticket_engine.TicketClass.exclude_fields

    return TicketSerializer


def ticket_detail_serializer_factory(ticket_engine):
    """
    Returns a serializer that includes all ticket data,
    including actions
    """

    class TicketSerializer(GetUserMixin, serializers.ModelSerializer):
        state_verbose = serializers.CharField(read_only=True)
        actions = serializers.SerializerMethodField()
        updates = serializers.SerializerMethodField()

        class Meta:
            model = ticket_engine.TicketClass
            exclude = ["polymorphic_ctype"] + ticket_engine.TicketClass.exclude_fields
            read_only_fields = ["ts", "user", "state", "last_update"]

        def get_actions(self, obj):
            actions = ticket_engine.get_ticket_actions(obj, self._get_current_user())
            return TicketActionSerializer(actions, many=True).data

        def get_updates(self, obj):
            updates = obj.get_all_updates()
            request = self.context.get("request")
            updates_data = []
            for ticket_update in updates:
                if not ticket_update.is_visible(request):
                    continue
                ticket_update_serializer_class = ticket_update.list_serializer
                if ticket_update_serializer_class and isinstance(
                    ticket_update_serializer_class, str
                ):
                    ticket_update_serializer_class = import_string(
                        ticket_update_serializer_class
                    )
                elif not ticket_update_serializer_class:
                    ticket_update_serializer_class = (
                        ticket_update_list_serializer_factory(type(ticket_update))
                    )
                serializer = ticket_update_serializer_class(ticket_update, context=self.context)
                updates_data.append(serializer.data)
            return updates_data

    return TicketSerializer


class AttachmentDownloadUrlMixin:
    """
    Represent the `file` of attachment ticket updates as the URL of the download endpoint of the API

    The download endpoint authenticates users as the other API endpoints, and checks the visibility
    of the ticket and of the ticket update, while the URL of the file in the storage is not subject
    to any check (and requires the storage to be served).
    As in HTML views, deleted attachments can only be downloaded by superusers.

    To be used in serializers of ticket updates, e.g. in a custom `list_serializer`.
    """

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if isinstance(instance, AttachmentTicketUpdate) and "file" in data:
            request = self.context.get("request")
            if not instance.file:
                data["file"] = None
            elif instance.deleted and (request is None or not request.user.is_superuser):
                data["file"] = None
            else:
                # In API views, the root name is in the URL: no query to load the ticket
                resolver_match = getattr(request, "resolver_match", None)
                root_name = resolver_match.kwargs.get("root_name") if resolver_match else None
                url = reverse(
                    "ticket-update-download",
                    kwargs={"root_name": root_name or instance.ticket.root_name, "pk": instance.pk},
                )
                data["file"] = request.build_absolute_uri(url) if request is not None else url
        return data


class LimitQuerysetsMixin:
    """
    Limit the choices of related fields as `limit_view_querysets(ticket)` does in HTML views

    To be used in serializers of ticket updates, e.g. in a custom `write_serializer`.
    The ticket shall be passed in serializer context, as action API view does:
    validation fails if it is missing.
    """

    def get_fields(self):
        fields = super().get_fields()
        ticket = self.context.get("ticket")
        if ticket is not None:
            limit_querysets = self.Meta.model.limit_view_querysets(ticket)
            for name, queryset in limit_querysets.items():
                field = fields[name]
                # Many-to-many fields wrap the related field
                getattr(field, "child_relation", field).queryset = queryset
        return fields

    def validate(self, attrs):
        if self.context.get("ticket") is None:
            raise ImproperlyConfigured(
                f"{type(self).__name__} requires a 'ticket' in serializer context"
            )
        return super().validate(attrs)


class TicketUpdateBaseCreateSerializer(
    AttachmentDownloadUrlMixin, LimitQuerysetsMixin, GetUserMixin, serializers.ModelSerializer
):

    def validate_file(self, value):
        """
        Apply the size limit of attachment ticket updates
        """
        model = self.Meta.model
        if value and issubclass(model, AttachmentTicketUpdate):
            try:
                model.validate_file_size(value)
            except DjangoValidationError as e:
                raise serializers.ValidationError(e.messages)
        return value

    def create(self, validated_data):
        user = self._get_current_user()
        validated_data["user"] = user if not user.is_anonymous else None
        return super().create(validated_data)


def ticket_update_create_serializer_factory(ticket_update_model):

    class TicketUpdateCreateSerializer(TicketUpdateBaseCreateSerializer):
        class Meta:
            model = ticket_update_model
            exclude = [
                "ts",
                "user",
                "seq",
                "previous",
                "polymorphic_ctype",
                "source_state",
                "target_state",
                "ticket",  # Ticket is passed in url
            ] + ticket_update_model.exclude_fields

    return TicketUpdateCreateSerializer


def ticket_update_list_serializer_factory(ticket_update_model, ticket_engine=None):

    class TicketUpdateListSerializer(AttachmentDownloadUrlMixin, serializers.ModelSerializer):
        ticket = (
            ticket_list_serializer_factory(ticket_engine)()
            if ticket_engine
            else serializers.PrimaryKeyRelatedField(read_only=True)
        )
        user_verbose = serializers.SerializerMethodField()

        def get_user_verbose(self, obj):
            if obj.user:
                return user_verbose(obj.user)
            return None

        class Meta:
            model = ticket_update_model
            exclude = [
                "seq",
                "previous",
                "polymorphic_ctype",
            ] + ticket_update_model.exclude_fields

    return TicketUpdateListSerializer
