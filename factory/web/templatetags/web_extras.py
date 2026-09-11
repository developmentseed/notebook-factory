import markdown as md
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def markdown(value):
    return mark_safe(md.markdown(value or "", extensions=["tables", "fenced_code"]))


@register.filter
def get(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.filter
def status_class(status):
    return {
        "published": "ok",
        "failed": "err",
        "cancelled": "muted",
        "queued": "muted",
    }.get(status, "active")


@register.simple_tag(takes_context=True)
def qs_replace(context, **kwargs):
    """Rebuild the current query string with some keys replaced."""
    request = context["request"]
    q = request.GET.copy()
    for k, v in kwargs.items():
        if v in (None, ""):
            q.pop(k, None)
        else:
            q[k] = v
    return "?" + q.urlencode() if q else "?"
