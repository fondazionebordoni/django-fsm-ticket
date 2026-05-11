# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import django_filters

from django_fsm_ticket.models import TicketUpdate


def ticket_filter_factory(ticket_model):
    class TicketFilter(django_filters.FilterSet):
        hide_closed = django_filters.BooleanFilter(method="filter_closed")

        def filter_closed(self, queryset, name, value):
            """if true, then show all, otherwise filter out closed ticket"""
            if value:
                queryset = queryset.exclude(state__in=ticket_model.closed_states)
            return queryset

        class Meta:
            model = ticket_model
            exclude = [
                "polymorphic_ctype",
            ] + ticket_model.exclude_fields

    return TicketFilter


class TicketUpdateFilter(django_filters.FilterSet):
    type = django_filters.CharFilter(method="filter_type")
    type__in = django_filters.CharFilter(method="filter_type_in")
    type__not = django_filters.CharFilter(method="filter_type_not")

    def filter_type(self, queryset, name, value):
        """get only updates of given type (model name all lowercase)"""
        if value:
            queryset = queryset.filter(polymorphic_ctype__model=value)
        return queryset

    def filter_type_in(self, queryset, name, value):
        """get updates of given types (model name all lowercase, comma separated)"""
        if value:
            models = value.split(",")
            queryset = queryset.filter(polymorphic_ctype__model__in=models)
        return queryset

    def filter_type_not(self, queryset, name, value):
        """get only updates of given type (model name all lowercase)"""
        if value:
            queryset = queryset.exclude(polymorphic_ctype__model=value)
        return queryset

    class Meta:
        model = TicketUpdate
        fields = {
            "notes": ["icontains", "exact"],
            "user__id": ["exact"],
            "ticket__id": ["exact"],
        }
