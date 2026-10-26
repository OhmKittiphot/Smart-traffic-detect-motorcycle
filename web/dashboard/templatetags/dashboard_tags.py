"""Template tags for the dashboard pages: {% load dashboard_tags %}."""
from django import template
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from dashboard.models import SEEK_STEPS_S, Language, SystemSettings, Units, UserPreferences

register = template.Library()


@register.inclusion_tag("dashboard/_settings_panel.html", takes_context=True)
def settings_panel(context):
    """The top bar's Settings pop-up, with only what the logged-in account may change, and the box the
    notifications of new violations appear in."""
    user = context["request"].user
    return {
        "user": user,
        "csrf_token": context.get("csrf_token"),   # for the Log out form
        "system": SystemSettings.load() if user.has_perm("dashboard.manage_settings") else None,
        "prefs": UserPreferences.load(user),
        "live_view": user.has_perm("dashboard.use_dashboard"),
        "records": user.has_perm("dashboard.view_records"),
        "cameras": user.has_perm("dashboard.view_system_status"),
        "seek_steps": SEEK_STEPS_S,
        "units": Units.choices,
        "languages": Language.choices,
    }


@register.simple_tag(takes_context=True)
def nav_link(context, view_name, label):
    """Top bar link; marked aria-current="page" on the page it points to."""
    match = context["request"].resolver_match
    current = format_html(' aria-current="page"') if match and match.view_name == view_name else ""
    return format_html('<a class="plate-link" href="{}"{}>{}</a>', reverse(view_name), current, label)


@register.simple_tag
def skeleton_rows(rows, cols):
    """Grey placeholder rows shown in a table body until its data loads."""
    cell = format_html('<td><span class="skeleton skeleton-cell"></span></td>')
    row = format_html('<tr class="skeleton-row">{}</tr>', format_html_join("", "{}", [(cell,)] * cols))
    return format_html_join("", "{}", [(row,)] * rows)
