from django import forms

from tests.models import OrderTicketUpdate


class OrderTicketUpdateForm(forms.ModelForm):
    def __init__(self, limit_querysets, ticket, *args, **kwargs):
        # Not using ticket here
        super().__init__(*args, **kwargs)
        for field, queryset in limit_querysets.items():
            self.fields[field].queryset = queryset

    class Meta:
        model = OrderTicketUpdate
        fields = ["price", "seller"]
