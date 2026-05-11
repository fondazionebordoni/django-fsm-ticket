# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django import template

register = template.Library()


@register.simple_tag
def render_ticket_update(ticket_update, request):
    return ticket_update.render(request=request)
