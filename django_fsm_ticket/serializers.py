# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from rest_framework import serializers

from django.contrib.auth.models import AnonymousUser
from django.utils.module_loading import import_string

from django_fsm_ticket.models import Ticket
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
                serializer = ticket_update_serializer_class(ticket_update)
                updates_data.append(serializer.data)
            return updates_data

    return TicketSerializer


class TicketUpdateBaseCreateSerializer(GetUserMixin, serializers.ModelSerializer):

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

    class TicketUpdateListSerializer(serializers.ModelSerializer):
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
